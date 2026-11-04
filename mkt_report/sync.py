"""Pull each source into the database. One sync_runs row per source per run.

The pull happens before anything is written, then the source's rows for the window are
replaced in one transaction. A failed pull therefore leaves the previous good data intact,
and a re-pull of an overlapping window corrects late-arriving numbers instead of duplicating them.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import List

from sqlalchemy import delete

from .db import TABLES, SyncRun, now_utc
from .sources.base import Source

log = logging.getLogger(__name__)


def sync_source(src: Source, Session, start: date, end: date) -> SyncRun:
    with Session() as s:
        run = SyncRun(source=src.name, started_at=now_utc(), status="running", window_start=start, window_end=end, counts={})
        s.add(run)
        s.commit()
        run_id = run.id
    calls_before = src.calls
    try:
        rows = src.fetch(start, end)
    except Exception as exc:
        with Session() as s:
            run = s.get(SyncRun, run_id)
            run.status, run.error, run.finished_at = "failed", f"{type(exc).__name__}: {exc}"[:2000], now_utc()
            run.api_calls = src.calls - calls_before
            s.commit()
        log.error("%s sync failed: %s", src.name, exc)
        raise
    with Session() as s:
        for table, items in rows.items():
            model = TABLES[table]
            if table == "ga4_weekly_users":
                weeks = [i["iso_week"] for i in items]
                if weeks:
                    s.execute(delete(model).where(model.iso_week.in_(weeks)))
            else:
                s.execute(delete(model).where(model.day >= start, model.day <= end))
            s.add_all(model(**i) for i in items)
        run = s.get(SyncRun, run_id)
        run.counts = {k: len(v) for k, v in rows.items()}
        run.api_calls = src.calls - calls_before
        # an empty answer is a problem to look at, never "a quiet week"
        run.status = "ok" if Source.total_rows(rows) else "empty"
        run.finished_at = now_utc()
        s.commit()
    log.info("%s sync %s: %s in %d API calls", src.name, run.status, run.counts, run.api_calls)
    return run


def run_sync(sources: List[Source], Session, start: date, end: date):
    """Sync every source; one failing never stops the others. Returns (runs, failures)."""
    runs, failures = [], []
    for src in sources:
        try:
            runs.append(sync_source(src, Session, start, end))
        except Exception as exc:
            failures.append((src, exc))
    return runs, failures
