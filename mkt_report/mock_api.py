"""An in-process stand-in for the three APIs, served from fixtures/demo_data.json.

It answers in the same shapes the real endpoints document:
  GA4      POST analyticsdata.googleapis.com/v1beta/properties/{id}:runReport
           dimensionHeaders / metricHeaders (with type) / rows of string values / rowCount / metadata / kind,
           limit + offset paging, no "rows" or "rowCount" when empty
  GSC      POST www.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query
           rows of keys / clicks / impressions / ctr (0-1) / position, startRow + rowLimit paging,
           anonymised queries left out of query rows but counted in date totals, final data lagging 3 days
  Meta     GET graph.facebook.com/v26.0/act_{id}/insights
           data[] with string numbers and actions / action_values / purchase_roas lists, paging.cursors + paging.next
Bearer tokens are checked, so a missing or wrong token fails the way the real API would.
Used by `--mock` runs and by the tests; nothing needs an account.
"""
from __future__ import annotations

import base64
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "demo_data.json"
GOOGLE_TOKEN = "mock-google-token"
META_TOKEN = "mock-meta-token"
ANONYMISED_SHARE = 0.18  # extra clicks/impressions from queries Google withholds for privacy

GA4_METRIC_TYPES = {"sessions": "TYPE_INTEGER", "totalUsers": "TYPE_INTEGER", "newUsers": "TYPE_INTEGER",
                    "engagedSessions": "TYPE_INTEGER", "keyEvents": "TYPE_FLOAT", "screenPageViews": "TYPE_INTEGER"}


class MockResponse:
    def __init__(self, status: int, body: Dict, headers: Optional[Dict] = None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.content = json.dumps(body).encode()
        self.text = self.content.decode()

    def json(self):
        return self._body


def _num(v) -> str:
    return f"{v:g}" if isinstance(v, float) else str(v)


def _google_error(code: int, status: str, message: str) -> MockResponse:
    return MockResponse(code, {"error": {"code": code, "message": message, "status": status}})


class MockAPISession:
    def __init__(self, fixture_path: Path = FIXTURE, fail_plan: Optional[Dict[str, List]] = None,
                 empty: Optional[set] = None):
        self.data = json.loads(Path(fixture_path).read_text())
        # e.g. {"meta": [500, 429]} returns those statuses before serving normally; ("x", body) for a custom body
        self.fail_plan = {k: list(v) for k, v in (fail_plan or {}).items()}
        self.empty = set(empty or ())  # sources that answer with no data, like a brand-new property
        self.log: List[str] = []

    @property
    def anchor(self) -> date:
        return date.fromisoformat(self.data["anchor"])

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        u = urlparse(url)
        params = dict(params or {})
        params.update({k: v[0] for k, v in parse_qs(u.query).items()})
        headers = headers or {}
        source = {"analyticsdata.googleapis.com": "ga4", "www.googleapis.com": "gsc", "graph.facebook.com": "meta"}.get(u.netloc)
        self.log.append(f"{method} {u.netloc}{u.path}")
        if source and self.fail_plan.get(source):
            item = self.fail_plan[source].pop(0)
            if isinstance(item, tuple):
                return MockResponse(item[0], item[1])
            return MockResponse(item, {"error": {"code": item, "message": "simulated failure"}}, {"Retry-After": "0"})
        if source in ("ga4", "gsc"):
            if headers.get("Authorization") != f"Bearer {GOOGLE_TOKEN}":
                return _google_error(401, "UNAUTHENTICATED", "Request had invalid authentication credentials.")
        if source == "ga4" and method == "POST":
            return self._ga4(u.path, json or {})
        if source == "gsc" and method == "POST":
            return self._gsc(u.path, json or {})
        if source == "meta" and method == "GET":
            if headers.get("Authorization") != f"Bearer {META_TOKEN}":
                return MockResponse(400, {"error": {"message": "Invalid OAuth access token - Cannot parse access token",
                                                    "type": "OAuthException", "code": 190, "fbtrace_id": "AbCdEf123"}})
            return self._meta(u.path, params)
        return MockResponse(404, {"error": {"message": f"mock has no route for {method} {url}"}})

    # ---------- GA4 ----------

    def _ga4(self, path: str, body: Dict) -> MockResponse:
        prop = path.rsplit("/", 1)[-1].split(":")[0]
        if prop != self.data["ga4_property"]:
            return _google_error(403, "PERMISSION_DENIED", "User does not have sufficient permissions for this property.")
        dims = [d["name"] for d in body.get("dimensions", [])]
        mets = [m["name"] for m in body.get("metrics", [])]
        rng = body["dateRanges"][0]
        lo, hi = date.fromisoformat(rng["startDate"]), date.fromisoformat(rng["endDate"])
        unknown = [m for m in mets if m not in GA4_METRIC_TYPES]
        if unknown:
            return _google_error(400, "INVALID_ARGUMENT", f"Field {unknown[0]} is not a valid metric.")

        def in_range(d):
            return lo <= date.fromisoformat(d) <= hi

        if dims == ["isoYearIsoWeek"]:
            weeks = {(date.fromisoformat(r["date"]).isocalendar()[0], date.fromisoformat(r["date"]).isocalendar()[1])
                     for r in self.data["ga4_daily"] if in_range(r["date"])}
            src = [r for r in self.data["ga4_weekly_users"]
                   if (int(r["isoYearIsoWeek"][:4]), int(r["isoYearIsoWeek"][4:])) in weeks]
        elif "pagePath" in dims:
            src = [r for r in self.data["ga4_pages"] if in_range(r["date"])]
        else:
            src = [r for r in self.data["ga4_daily"] if in_range(r["date"])]
        if "ga4" in self.empty:
            src = []
        rows = [{"dimensionValues": [{"value": r[d].replace("-", "") if d == "date" else r[d]} for d in dims],
                 "metricValues": [{"value": _num(r[m])} for m in mets]} for r in src]
        rows.sort(key=lambda r: [v["value"] for v in r["dimensionValues"]])
        offset, limit = int(body.get("offset", 0)), int(body.get("limit", 10000))
        resp = {"dimensionHeaders": [{"name": d} for d in dims],
                "metricHeaders": [{"name": m, "type": GA4_METRIC_TYPES[m]} for m in mets],
                "metadata": {"currencyCode": self.data["currency"], "timeZone": self.data["time_zone"]},
                "kind": "analyticsData#runReport"}
        if rows:
            resp["rows"] = rows[offset:offset + limit]
            resp["rowCount"] = len(rows)
        return MockResponse(200, resp)

    # ---------- Search Console ----------

    def _gsc(self, path: str, body: Dict) -> MockResponse:
        site = unquote(path.split("/sites/")[1].split("/searchAnalytics")[0])
        if site != self.data["site"]:
            return _google_error(403, "PERMISSION_DENIED", f"User does not have sufficient permission for site '{site}'.")
        if int(body.get("rowLimit", 1000)) > 25000:
            return _google_error(400, "INVALID_ARGUMENT", "rowLimit must be between 1 and 25000")
        lo, hi = date.fromisoformat(body["startDate"]), date.fromisoformat(body["endDate"])
        dims = body.get("dimensions", [])
        src = [] if "gsc" in self.empty else [r for r in self.data["gsc"] if lo <= date.fromisoformat(r["date"]) <= hi]
        if dims == ["date"]:
            agg = defaultdict(lambda: [0.0, 0.0, 0.0])
            for r in src:
                a = agg[r["date"]]
                a[0] += r["clicks"]
                a[1] += r["impressions"]
                a[2] += r["position"] * r["impressions"]
            rows = []
            for d in sorted(agg):
                c, i, pi = agg[d]
                c, i = round(c * (1 + ANONYMISED_SHARE)), round(i * (1 + ANONYMISED_SHARE))
                rows.append({"keys": [d], "clicks": c, "impressions": i, "ctr": c / i if i else 0,
                             "position": pi / agg[d][1] if agg[d][1] else 0})
        else:
            rows = [{"keys": [r[k] for k in dims], "clicks": r["clicks"], "impressions": r["impressions"],
                     "ctr": r["clicks"] / r["impressions"], "position": r["position"]} for r in src]
            rows.sort(key=lambda r: (r["keys"][0], -r["clicks"], r["keys"][-1]))
        start, limit = int(body.get("startRow", 0)), int(body.get("rowLimit", 1000))
        page = rows[start:start + limit]
        resp = {"responseAggregationType": "byProperty"}
        if page:
            resp["rows"] = page
        return MockResponse(200, resp)

    # ---------- Meta Ads ----------

    def _meta(self, path: str, params: Dict) -> MockResponse:
        parts = path.strip("/").split("/")
        if len(parts) != 3 or parts[2] != "insights" or not parts[0].startswith("v"):
            return MockResponse(400, {"error": {"message": "Unknown path components", "type": "OAuthException", "code": 2500}})
        if parts[1] != f"act_{self.data['meta_ad_account']}":
            return MockResponse(400, {"error": {"message": "Unsupported get request. Object does not exist or you lack permission",
                                                "type": "GraphMethodException", "code": 100, "error_subcode": 33}})
        tr = json.loads(params["time_range"])
        lo, hi = date.fromisoformat(tr["since"]), date.fromisoformat(tr["until"])
        src = [] if "meta" in self.empty else [r for r in self.data["meta"] if lo <= date.fromisoformat(r["date"]) <= hi]
        src.sort(key=lambda r: (r["date"], r["campaign_id"]))
        fields = params.get("fields", "").split(",")
        out = []
        for r in src:
            row = {"campaign_id": r["campaign_id"], "campaign_name": r["campaign_name"],
                   "spend": f"{r['spend']:.2f}", "impressions": str(r["impressions"]), "clicks": str(r["clicks"]),
                   "cpc": f"{r['spend'] / r['clicks']:.6f}" if r["clicks"] else None,
                   "date_start": r["date"], "date_stop": r["date"]}
            actions = [{"action_type": "link_click", "value": str(r["link_clicks"])},
                       {"action_type": "landing_page_view", "value": str(r["landing_page_views"])},
                       {"action_type": "page_engagement", "value": str(r["link_clicks"] + 3)}]
            values = []
            if r["conversions"]:
                kinds = ["purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase"] if r["action"] == "purchase" \
                    else ["lead", "onsite_conversion.lead_grouped"]
                actions += [{"action_type": k, "value": str(r["conversions"])} for k in kinds]
                if r["value"]:
                    values = [{"action_type": k, "value": f"{r['value']:.2f}"} for k in kinds]
            row["actions"] = actions
            if values:
                row["action_values"] = values
                row["purchase_roas"] = [{"action_type": "omni_purchase", "value": f"{r['value'] / r['spend']:.6f}"}]
            out.append({k: v for k, v in row.items() if v is not None and (k in fields or k.startswith("date_"))})
        limit = int(params.get("limit", 25))
        start = int(base64.b64decode(params["after"]).decode()) if params.get("after") else 0
        page = out[start:start + limit]
        resp = {"data": page}
        if page:
            resp["paging"] = {"cursors": {"before": base64.b64encode(str(start).encode()).decode(),
                                          "after": base64.b64encode(str(start + len(page)).encode()).decode()}}
            if start + limit < len(out):
                q = {k: v for k, v in params.items() if k != "after"}
                q["after"] = resp["paging"]["cursors"]["after"]
                resp["paging"]["next"] = f"https://graph.facebook.com{path}?{urlencode(q, quote_via=quote)}"
        return MockResponse(200, resp)
