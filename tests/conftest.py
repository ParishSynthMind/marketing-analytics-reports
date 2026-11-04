import os

import pytest

from mkt_report.db import make_session_factory
from mkt_report.http import Transport
from mkt_report.mock_api import GOOGLE_TOKEN, META_TOKEN, MockAPISession
from mkt_report.sources import GA4Source, GSCSource, MetaAdsSource
from mkt_report.sources.meta import meta_should_retry


@pytest.fixture
def api():
    return MockAPISession()


def google(token=GOOGLE_TOKEN):
    return lambda: {"Authorization": f"Bearer {token}"}


def meta(token=META_TOKEN):
    return lambda: {"Authorization": f"Bearer {token}"}


def make_sources(api, ga4_page=400, gsc_page=500, meta_page=100, max_retries=4):
    nap = lambda s: None  # noqa: E731
    return [
        GA4Source(Transport("ga4", google(), api, sleep=nap, max_retries=max_retries), api.data["ga4_property"], page_size=ga4_page),
        GSCSource(Transport("gsc", google(), api, sleep=nap, max_retries=max_retries), api.data["site"], page_size=gsc_page),
        MetaAdsSource(Transport("meta", meta(), api, sleep=nap, also_retry=meta_should_retry, max_retries=max_retries),
                      api.data["meta_ad_account"], page_size=meta_page),
    ]


@pytest.fixture
def Session(tmp_path):
    """SQLite by default. Set TEST_DATABASE_URL (e.g. a Postgres URL) to run the same tests on that database."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        return make_session_factory(f"sqlite:///{tmp_path / 'test.db'}")
    from sqlalchemy import create_engine

    from mkt_report.db import Base
    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    engine.dispose()
    return make_session_factory(url)


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    for var in ("SMTP_HOST", "REPORT_EMAIL_TO", "ALERT_WEBHOOK_URL", "ANTHROPIC_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS",
                "GOOGLE_OAUTH_ACCESS_TOKEN", "META_ACCESS_TOKEN", "GA4_PROPERTY_ID", "GSC_SITE_URL", "META_AD_ACCOUNT_ID",
                "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
