"""Google Search Console through the Search Analytics API `searchanalytics.query` (webmasters.readonly scope).

Two queries per sync:
  daily totals  dimensions [date]          clicks, impressions, ctr, position for the whole site
  queries       dimensions [date, query]   the same per search query
Totals come from the date-only query on purpose: Google leaves anonymised queries out of
query-level rows, so adding up the query rows would understate the site's real clicks.
Pagination uses startRow/rowLimit (max 25,000) until a page comes back short.
"""
from __future__ import annotations

from datetime import date
from typing import Dict, Iterator, List
from urllib.parse import quote

from ..http import MAX_PAGES, SourceError
from .base import Rows, Source

BASE_URL = "https://www.googleapis.com/webmasters/v3"
MAX_ROW_LIMIT = 25000


class GSCSource(Source):
    name, label = "gsc", "Google Search Console"

    def __init__(self, transport, site_url: str, base_url: str = BASE_URL, page_size: int = MAX_ROW_LIMIT,
                 max_query_rows: int = 100000, search_type: str = "web", data_state: str = "final"):
        super().__init__(transport)
        if not site_url:
            raise ValueError("Search Console property is missing "
                             "(set GSC_SITE_URL, e.g. https://www.example.com/ or sc-domain:example.com)")
        self.site_url = site_url
        self.url = f"{base_url.rstrip('/')}/sites/{quote(site_url, safe='')}/searchAnalytics/query"
        self.page_size = min(page_size, MAX_ROW_LIMIT)
        self.max_query_rows = max_query_rows
        self.search_type, self.data_state = search_type, data_state

    def query(self, start: date, end: date, dimensions: List[str], max_rows: int) -> Iterator[Dict]:
        start_row = 0
        for _ in range(MAX_PAGES):
            body = {"startDate": start.isoformat(), "endDate": end.isoformat(), "dimensions": dimensions,
                    "type": self.search_type, "dataState": self.data_state,
                    "rowLimit": self.page_size, "startRow": start_row}
            resp = self.t.request("POST", self.url, json=body, where=f"searchAnalytics.query[{','.join(dimensions)}]")
            rows = resp.get("rows", [])  # no "rows" key at all when there is no data
            for r in rows:
                rec = dict(zip(dimensions, r.get("keys", [])))
                rec.update(clicks=float(r.get("clicks", 0)), impressions=float(r.get("impressions", 0)),
                           ctr=float(r.get("ctr", 0)), position=float(r.get("position", 0)))
                yield rec
            start_row += len(rows)
            if len(rows) < self.page_size or start_row >= max_rows:
                return
        raise SourceError(self.name, 0, "searchAnalytics.query", f"more than {MAX_PAGES} pages, stopping")

    def fetch(self, start: date, end: date) -> Rows:
        daily = [{"day": date.fromisoformat(r["date"]), "clicks": r["clicks"], "impressions": r["impressions"],
                  "ctr": r["ctr"], "position": r["position"]}
                 for r in self.query(start, end, ["date"], max_rows=10000)]
        queries = [{"day": date.fromisoformat(r["date"]), "query": r["query"][:700], "clicks": r["clicks"],
                    "impressions": r["impressions"], "position": r["position"]}
                   for r in self.query(start, end, ["date", "query"], max_rows=self.max_query_rows)]
        return {"gsc_daily": daily, "gsc_queries_daily": queries}
