from datetime import date

import pytest

from mkt_report.http import SourceError, Transport
from mkt_report.mock_api import MockAPISession, MockResponse
from mkt_report.sources import GA4Source, GSCSource, MetaAdsSource
from mkt_report.sources.ga4 import parse_report
from mkt_report.sources.meta import meta_should_retry, sum_actions

from .conftest import google, make_sources, meta

START, END = date(2026, 10, 19), date(2026, 11, 1)


def test_ga4_parse_report_types_numbers_from_metric_headers():
    body = {"dimensionHeaders": [{"name": "date"}],
            "metricHeaders": [{"name": "sessions", "type": "TYPE_INTEGER"}, {"name": "keyEvents", "type": "TYPE_FLOAT"}],
            "rows": [{"dimensionValues": [{"value": "20261101"}], "metricValues": [{"value": "412"}, {"value": "7.5"}]}],
            "rowCount": 1}
    assert parse_report(body) == [{"date": "20261101", "sessions": 412, "keyEvents": 7.5}]
    assert parse_report({"dimensionHeaders": [], "metricHeaders": [], "kind": "analyticsData#runReport"}) == []


def test_ga4_paginates_with_offset_until_row_count(api):
    ga4 = make_sources(api, ga4_page=50)[0]
    rows = ga4.fetch(START, END)
    assert len(rows["ga4_daily"]) == 14
    assert len(rows["ga4_pages_daily"]) == 14 * 12  # every page every day, spread over several pages of 50
    assert ga4.calls == 1 + 1 + 4  # daily, weekly, pages (168 rows / 50 = 4 calls)
    assert rows["ga4_daily"][0]["day"] == START and isinstance(rows["ga4_daily"][0]["sessions"], int)
    assert {w["iso_week"] for w in rows["ga4_weekly_users"]} == {"202643", "202644"}


def test_ga4_wrong_property_is_a_permission_error(api):
    t = Transport("ga4", google(), api, sleep=lambda s: None)
    with pytest.raises(SourceError, match="403.*sufficient permissions"):
        GA4Source(t, "999").fetch(START, END)


def test_ga4_bad_token_is_unauthenticated(api):
    t = Transport("ga4", google("wrong"), api, sleep=lambda s: None)
    with pytest.raises(SourceError, match="401"):
        GA4Source(t, api.data["ga4_property"]).fetch(START, END)


def test_missing_ids_are_explained():
    with pytest.raises(ValueError, match="GA4_PROPERTY_ID"):
        GA4Source(None, "")
    with pytest.raises(ValueError, match="GSC_SITE_URL"):
        GSCSource(None, "")
    with pytest.raises(ValueError, match="META_AD_ACCOUNT_ID"):
        MetaAdsSource(None, "")


def test_gsc_paginates_and_keeps_site_totals_separate_from_queries(api):
    gsc = make_sources(api, gsc_page=100)[1]
    rows = gsc.fetch(START, END)
    days = {r["day"] for r in rows["gsc_daily"]}
    assert max(days) == date(2026, 10, 29)  # the mock's final data trails by 3 days, like the real thing
    total_clicks = sum(r["clicks"] for r in rows["gsc_daily"])
    query_clicks = sum(r["clicks"] for r in rows["gsc_queries_daily"])
    assert total_clicks > query_clicks  # anonymised queries only show up in the totals
    assert all(0 <= r["ctr"] <= 1 for r in rows["gsc_daily"])
    assert gsc.calls >= 3  # query rows (11 days x 15 queries) needed more than one page of 100


def test_gsc_url_encodes_domain_properties():
    s = GSCSource(None, "sc-domain:example.com")
    assert s.url.endswith("/sites/sc-domain%3Aexample.com/searchAnalytics/query")
    assert GSCSource(None, "https://www.example.com/", page_size=99999).page_size == 25000


def test_meta_parses_string_numbers_and_follows_paging_next(api):
    m = make_sources(api, meta_page=10)[2]
    rows = m.fetch(START, END)["meta_daily"]
    assert len(rows) == 14 * 3
    assert m.calls == 5  # 42 rows / 10 per page
    r = rows[0]
    assert isinstance(r["spend"], float) and isinstance(r["impressions"], int)
    assert {"spend", "clicks", "conversions", "conversion_value", "purchase_roas"} <= set(r)


def test_meta_counts_one_conversion_family_not_three(api):
    raw = [x for x in api.data["meta"] if START.isoformat() <= x["date"] <= END.isoformat()
           and x["action"] == "purchase" and x["conversions"] >= 2][0]
    day = date.fromisoformat(raw["date"])
    m = make_sources(api)[2]
    row = [r for r in m.fetch(day, day)["meta_daily"] if r["campaign_id"] == raw["campaign_id"]][0]
    assert row["conversions"] == raw["conversions"]  # not 3x (purchase + omni_purchase + pixel purchase)
    assert row["conversion_value"] == pytest.approx(raw["value"])


def test_meta_lead_campaigns_counted_when_configured(api):
    t = Transport("meta", meta(), api, sleep=lambda s: None)
    m = MetaAdsSource(t, api.data["meta_ad_account"], conversion_actions=["lead"])
    rows = [r for r in m.fetch(START, END)["meta_daily"] if r["campaign_id"].endswith("103")]
    expected = sum(x["conversions"] for x in api.data["meta"]
                   if x["campaign_id"].endswith("103") and START.isoformat() <= x["date"] <= END.isoformat())
    assert sum(r["conversions"] for r in rows) == expected


def test_meta_rate_limit_400_is_retried_but_bad_token_is_not():
    assert meta_should_retry(MockResponse(400, {"error": {"code": 17, "message": "User request limit reached"}}))
    assert meta_should_retry(MockResponse(400, {"error": {"code": 1, "is_transient": True}}))
    assert not meta_should_retry(MockResponse(400, {"error": {"code": 190, "message": "Invalid OAuth access token"}}))
    assert not meta_should_retry(MockResponse(500, {}))  # 5xx is retried by the transport itself

    api = MockAPISession(fail_plan={"meta": [(400, {"error": {"code": 17, "message": "User request limit reached"}})]})
    m = make_sources(api)[2]
    assert len(m.fetch(START, END)["meta_daily"]) == 42 and m.calls >= 2

    t = Transport("meta", meta("wrong"), MockAPISession(), sleep=lambda s: pytest.fail("should not retry"), also_retry=meta_should_retry)
    with pytest.raises(SourceError, match="Invalid OAuth access token"):
        MetaAdsSource(t, "1234567890").fetch(START, END)


def test_sum_actions_handles_missing_and_junk_values():
    assert sum_actions(None, ["purchase"]) == 0
    assert sum_actions([{"action_type": "purchase", "value": "2"}, {"action_type": "purchase", "value": "x"},
                        {"action_type": "lead", "value": "5"}], ["purchase"]) == 2
