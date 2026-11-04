from datetime import date, timedelta

from mkt_report.db import GA4Daily, GSCDaily, MetaDaily, SyncRun
from mkt_report.kpis import compute_kpis, pct_change, report_week, sync_window
from mkt_report.mock_api import MockAPISession
from mkt_report.sync import run_sync

from .conftest import make_sources

AS_OF = date(2026, 11, 2)  # the mock's "today", a Monday


def _sync(api, Session, as_of=AS_OF, weeks=9):
    start, end = sync_window(as_of, weeks)
    return run_sync(make_sources(api), Session, start, end)


def test_week_maths():
    assert report_week(date(2026, 11, 2)) == date(2026, 10, 26)  # Monday: last full week
    assert report_week(date(2026, 11, 8)) == date(2026, 10, 26)  # Sunday: still the week before
    assert sync_window(date(2026, 11, 2), 2) == (date(2026, 10, 19), date(2026, 11, 1))
    assert pct_change(110, 100) == 10.0 and pct_change(5, 0) is None and pct_change(None, 3) is None


def test_sync_all_sources_and_resync_does_not_duplicate(api, Session):
    runs, failures = _sync(api, Session)
    assert not failures and [r.status for r in runs] == ["ok", "ok", "ok"]
    with Session() as s:
        n = (s.query(GA4Daily).count(), s.query(GSCDaily).count(), s.query(MetaDaily).count())
    runs, failures = _sync(MockAPISession(), Session)
    with Session() as s:
        assert (s.query(GA4Daily).count(), s.query(GSCDaily).count(), s.query(MetaDaily).count()) == n
        assert s.query(SyncRun).count() == 6


def test_resync_corrects_late_arriving_numbers(api, Session):
    _sync(api, Session)
    later = MockAPISession()
    for r in later.data["ga4_daily"]:
        if r["date"] == "2026-10-28":
            r["sessions"] += 100
    _sync(later, Session)
    with Session() as s:
        before = next(r for r in api.data["ga4_daily"] if r["date"] == "2026-10-28")["sessions"]
        assert s.get(GA4Daily, date(2026, 10, 28)).sessions == before + 100


def test_one_failing_source_does_not_stop_the_others_and_keeps_old_data(api, Session):
    _sync(api, Session)
    broken = MockAPISession(fail_plan={"gsc": [500] * 10})
    start, end = sync_window(AS_OF, 9)
    runs, failures = run_sync(make_sources(broken, max_retries=1), Session, start, end)
    assert [r.source for r in runs] == ["ga4", "meta"]
    assert [f[0].name for f in failures] == ["gsc"]
    with Session() as s:
        assert s.query(GSCDaily).count() > 0  # previous good data untouched
        failed = s.query(SyncRun).filter_by(source="gsc", status="failed").one()
        assert "500" in failed.error and failed.api_calls == 2


def test_empty_answer_is_status_empty_not_ok(Session):
    api = MockAPISession(empty={"meta"})
    runs, _ = _sync(api, Session)
    assert {r.source: r.status for r in runs} == {"ga4": "ok", "gsc": "ok", "meta": "empty"}


def test_kpis_match_hand_calculation(api, Session):
    _sync(api, Session)
    with Session() as s:
        k = compute_kpis(s, AS_OF, weeks=8)
    assert k["report_week"] == {"start": "2026-10-26", "end": "2026-11-01"}
    cur = [r for r in api.data["ga4_daily"] if "2026-10-26" <= r["date"] <= "2026-11-01"]
    prev = [r for r in api.data["ga4_daily"] if "2026-10-19" <= r["date"] <= "2026-10-25"]
    g = k["ga4"]
    assert g["this_week"]["sessions"] == sum(r["sessions"] for r in cur)
    assert g["change_pct"]["sessions"] == round((sum(r["sessions"] for r in cur) - sum(r["sessions"] for r in prev))
                                                / sum(r["sessions"] for r in prev) * 100, 1)
    assert g["this_week"]["users"] == [w for w in api.data["ga4_weekly_users"] if w["isoYearIsoWeek"] == "202644"][0]["totalUsers"]
    assert len(g["weekly"]) == 8 and g["top_pages"][0]["page"] == "/"

    m = k["meta"]
    mc = [r for r in api.data["meta"] if "2026-10-26" <= r["date"] <= "2026-11-01"]
    spend = sum(r["spend"] for r in mc)
    value = sum(r["value"] for r in mc)
    assert m["this_week"]["spend"] == round(spend, 2)
    assert m["this_week"]["roas"] == round(value / spend, 2)
    assert m["this_week"]["cpc"] == round(spend / sum(r["clicks"] for r in mc), 2)
    lead = [c for c in m["campaigns"] if c["campaign"].startswith("Lead Magnet")][0]
    assert lead["roas"] is None  # no conversion value reported, so no ROAS rather than 0


def test_search_console_partial_week_is_compared_like_for_like(api, Session):
    _sync(api, Session)
    with Session() as s:
        k = compute_kpis(s, AS_OF)
    g = k["gsc"]
    assert g["this_week"]["days_with_data"] == 4 and g["compared_days"] == 4  # Mon-Thu only, data lags 3 days
    assert k["sources"]["gsc"]["report_week_days"] == 4
    with Session() as s:
        cur = sum(r.clicks for r in s.query(GSCDaily) if date(2026, 10, 26) <= r.day <= date(2026, 10, 29))
        base = sum(r.clicks for r in s.query(GSCDaily) if date(2026, 10, 19) <= r.day <= date(2026, 10, 22))
    assert g["change_pct"]["clicks"] == round((cur - base) / base * 100, 1)
    assert g["prev_week"]["days_with_data"] == 7  # the full previous week is still shown as-is


def test_missing_source_is_none_not_zero(Session):
    api = MockAPISession()
    start, end = sync_window(AS_OF, 9)
    run_sync(make_sources(api)[:1], Session, start, end)  # GA4 only
    with Session() as s:
        k = compute_kpis(s, AS_OF)
    assert k["ga4"] is not None and k["gsc"] is None and k["meta"] is None
    assert k["sources"]["gsc"] == {"connected": False, "last_sync": None, "last_status": None, "label": "Google Search Console",
                                   "has_report_week": False, "report_week_days": 0}


def test_single_week_report_works(api, Session):
    _sync(api, Session)
    with Session() as s:
        k = compute_kpis(s, AS_OF, weeks=1)
    assert k["ga4"]["prev_week"] is None and k["ga4"]["change_pct"]["sessions"] is None
    assert k["ga4"]["top_pages"] and k["gsc"]["top_queries"]


def test_old_data_only_means_no_report_week(api, Session):
    _sync(api, Session)
    with Session() as s:
        k = compute_kpis(s, AS_OF + timedelta(days=28))
    assert k["ga4"] is None and k["meta"] is None
    assert k["sources"]["ga4"]["connected"] is True and k["sources"]["ga4"]["has_report_week"] is False
