"""Google Analytics 4 through the Data API v1beta `properties/{id}:runReport` (read-only scope).

Three small reports per sync:
  daily totals   dimensions [date]            metrics sessions, totalUsers, newUsers, engagedSessions, keyEvents
  weekly users   dimensions [isoYearIsoWeek]  metric totalUsers (de-duplicated across the week)
  pages          dimensions [date, pagePath]  metrics screenPageViews, sessions
Pagination uses limit/offset until offset reaches the response's rowCount.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Dict, Iterator, List

from ..http import MAX_PAGES, SourceError
from .base import Rows, Source

BASE_URL = "https://analyticsdata.googleapis.com/v1beta"
INT_TYPES = {"TYPE_INTEGER"}


def parse_report(body: Dict) -> List[Dict]:
    """Turn a RunReportResponse into plain dicts keyed by dimension/metric name, numbers typed from metricHeaders."""
    dims = [h["name"] for h in body.get("dimensionHeaders", [])]
    mets = [(h["name"], h.get("type", "TYPE_FLOAT")) for h in body.get("metricHeaders", [])]
    out = []
    for row in body.get("rows", []):  # GA4 leaves out "rows" (and rowCount) entirely when there is no data
        rec = {d: v.get("value") for d, v in zip(dims, row.get("dimensionValues", []))}
        for (name, typ), v in zip(mets, row.get("metricValues", [])):
            raw = v.get("value")
            rec[name] = (int(float(raw)) if typ in INT_TYPES else float(raw)) if raw not in (None, "") else 0
        out.append(rec)
    return out


class GA4Source(Source):
    name, label = "ga4", "Google Analytics 4"

    def __init__(self, transport, property_id: str, base_url: str = BASE_URL, page_size: int = 10000):
        super().__init__(transport)
        if not property_id:
            raise ValueError("GA4 property ID is missing (set GA4_PROPERTY_ID, the number in Admin > Property details)")
        self.property_id = str(property_id).replace("properties/", "")
        self.url = f"{base_url.rstrip('/')}/properties/{self.property_id}:runReport"
        self.page_size = page_size

    def run_report(self, start: date, end: date, dimensions: List[str], metrics: List[str]) -> Iterator[Dict]:
        offset = 0
        for _ in range(MAX_PAGES):
            body = {
                "dateRanges": [{"startDate": start.isoformat(), "endDate": end.isoformat()}],
                "dimensions": [{"name": d} for d in dimensions],
                "metrics": [{"name": m} for m in metrics],
                "limit": str(self.page_size),
                "offset": str(offset),
            }
            resp = self.t.request("POST", self.url, json=body, where=f"runReport[{','.join(dimensions)}]")
            rows = parse_report(resp)
            yield from rows
            offset += len(rows)
            if not rows or offset >= int(resp.get("rowCount", 0) or 0):
                return
        raise SourceError(self.name, 0, "runReport", f"more than {MAX_PAGES} pages, stopping")

    def fetch(self, start: date, end: date) -> Rows:
        daily = [{
            "day": _ga_date(r["date"]), "sessions": r["sessions"], "total_users": r["totalUsers"],
            "new_users": r["newUsers"], "engaged_sessions": r["engagedSessions"], "key_events": float(r["keyEvents"]),
        } for r in self.run_report(start, end, ["date"], ["sessions", "totalUsers", "newUsers", "engagedSessions", "keyEvents"])]
        weekly = [{"iso_week": r["isoYearIsoWeek"], "total_users": r["totalUsers"]}
                  for r in self.run_report(start, end, ["isoYearIsoWeek"], ["totalUsers"])]
        pages = [{"day": _ga_date(r["date"]), "page_path": (r["pagePath"] or "(not set)")[:700],
                  "views": r["screenPageViews"], "sessions": r["sessions"]}
                 for r in self.run_report(start, end, ["date", "pagePath"], ["screenPageViews", "sessions"])]
        return {"ga4_daily": daily, "ga4_weekly_users": weekly, "ga4_pages_daily": pages}


def _ga_date(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()
