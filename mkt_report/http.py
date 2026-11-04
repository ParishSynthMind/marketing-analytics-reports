"""Shared HTTP transport for every source: retries with backoff on 429 and 5xx, clear errors, no secrets in logs."""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional

import requests

log = logging.getLogger(__name__)
MAX_PAGES = 500  # hard stop so a pagination bug can never loop forever


class SourceError(RuntimeError):
    def __init__(self, source: str, status: int, where: str, message: str):
        super().__init__(f"{source} API {status} on {where}: {message}")
        self.source, self.status, self.where = source, status, where


def _error_message(resp) -> str:
    try:
        body = resp.json()
    except Exception:
        return (getattr(resp, "text", "") or "")[:300]
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):  # Google: {"error": {"code", "message", "status"}}; Meta: {"error": {"message", "type", "code"}}
        return str(err.get("message") or err)[:300]
    return str(err or body)[:300]


class Transport:
    """One per source. `session` can be a requests.Session or the in-process mock."""

    def __init__(self, source: str, auth_header: Callable[[], Dict[str, str]], session: Optional[Any] = None,
                 max_retries: int = 4, sleep: Callable[[float], None] = time.sleep, timeout: float = 60.0,
                 also_retry: Optional[Callable[[Any], bool]] = None):
        self.source = source
        self.also_retry = also_retry  # e.g. Meta sends rate limits as HTTP 400 with error code 17 / 80004
        self._auth_header = auth_header
        self.session = session or requests.Session()
        self.max_retries = max_retries
        self.sleep = sleep
        self.timeout = timeout
        self.calls = 0

    def __repr__(self) -> str:  # never show a token in a traceback
        return f"Transport({self.source!r})"

    def request(self, method: str, url: str, params=None, json=None, where: str = "") -> Dict:
        where = where or url.split("?")[0]
        resp = None
        for attempt in range(self.max_retries + 1):
            self.calls += 1
            headers = {"Accept": "application/json", **self._auth_header()}
            resp = self.session.request(method, url, params=params, json=json, headers=headers, timeout=self.timeout)
            if resp.status_code == 429 or resp.status_code >= 500 or (self.also_retry and self.also_retry(resp)):
                if attempt == self.max_retries:
                    break
                ra = resp.headers.get("Retry-After")
                wait = float(ra) if ra and ra.replace(".", "", 1).isdigit() else 2 ** attempt
                log.warning("%s %s on %s, retry %d in %.0fs", self.source, resp.status_code, where, attempt + 1, wait)
                self.sleep(min(wait, 60.0))
                continue
            if resp.status_code >= 400:
                raise SourceError(self.source, resp.status_code, where, _error_message(resp))
            return resp.json() if resp.content else {}
        raise SourceError(self.source, resp.status_code, where, "gave up after retries: " + _error_message(resp))
