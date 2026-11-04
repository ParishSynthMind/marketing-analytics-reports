"""Meta (Facebook / Instagram) Ads through the Marketing API insights edge (ads_read permission).

One call shape per sync:
  GET /{version}/act_{ad_account_id}/insights
      level=campaign, time_increment=1, time_range={"since","until"},
      fields=campaign_id,campaign_name,spend,impressions,clicks,cpc,actions,action_values,purchase_roas
Numbers come back as strings; actions / action_values / purchase_roas are lists of
{"action_type": ..., "value": "..."}. Pagination follows paging.next until it is gone.
Days or campaigns with no delivery are simply absent, so an account with no spend returns data: [].
"""
from __future__ import annotations

import json
from datetime import date
from typing import Dict, Iterable, Iterator, List, Optional

from ..http import MAX_PAGES, SourceError
from .base import Rows, Source

BASE_URL = "https://graph.facebook.com"
DEFAULT_VERSION = "v26.0"
FIELDS = "campaign_id,campaign_name,spend,impressions,clicks,cpc,actions,action_values,purchase_roas"
# Meta reports rate limiting as HTTP 400 with these error codes (plus is_transient on temporary errors)
RATE_LIMIT_CODES = {4, 17, 32, 613} | set(range(80000, 80015))


def meta_should_retry(resp) -> bool:
    if resp.status_code != 400:
        return False
    try:
        err = resp.json().get("error", {})
    except Exception:
        return False
    return err.get("code") in RATE_LIMIT_CODES or bool(err.get("is_transient"))


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def sum_actions(items: Optional[List[Dict]], wanted: Iterable[str]) -> float:
    wanted = set(wanted)
    return sum(_num(a.get("value")) for a in (items or []) if a.get("action_type") in wanted)


class MetaAdsSource(Source):
    name, label = "meta", "Meta Ads"

    def __init__(self, transport, ad_account_id: str, version: str = DEFAULT_VERSION, base_url: str = BASE_URL,
                 conversion_actions: Iterable[str] = ("purchase",), page_size: int = 500):
        super().__init__(transport)
        if not ad_account_id:
            raise ValueError("Meta ad account ID is missing (set META_AD_ACCOUNT_ID, the number after act_)")
        self.account = str(ad_account_id).replace("act_", "")
        self.url = f"{base_url.rstrip('/')}/{version}/act_{self.account}/insights"
        # Meta lists the same purchase under several types (purchase, omni_purchase, offsite_conversion.fb_pixel_purchase),
        # so pick ONE family per account rather than adding them all up.
        self.conversion_actions = [a.strip() for a in conversion_actions if a.strip()]
        self.page_size = page_size

    def insights(self, start: date, end: date) -> Iterator[Dict]:
        url: Optional[str] = self.url
        params: Optional[Dict] = {
            "level": "campaign", "time_increment": "1", "fields": FIELDS, "limit": str(self.page_size),
            "time_range": json.dumps({"since": start.isoformat(), "until": end.isoformat()}),
        }
        for _ in range(MAX_PAGES):
            resp = self.t.request("GET", url, params=params, where="insights")
            yield from resp.get("data", [])
            url = (resp.get("paging") or {}).get("next")
            params = None  # paging.next is a complete URL with every parameter already in it
            if not url:
                return
        raise SourceError(self.name, 0, "insights", f"more than {MAX_PAGES} pages, stopping")

    def fetch(self, start: date, end: date) -> Rows:
        rows = []
        for r in self.insights(start, end):
            roas = r.get("purchase_roas") or []
            rows.append({
                "day": date.fromisoformat(r["date_start"]),
                "campaign_id": str(r.get("campaign_id") or "account"),
                "campaign_name": r.get("campaign_name"),
                "spend": _num(r.get("spend")),
                "impressions": int(_num(r.get("impressions"))),
                "clicks": int(_num(r.get("clicks"))),
                "conversions": sum_actions(r.get("actions"), self.conversion_actions),
                "conversion_value": sum_actions(r.get("action_values"), self.conversion_actions),
                "purchase_roas": _num(roas[0].get("value")) if roas else None,
            })
        return {"meta_daily": rows}
