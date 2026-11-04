from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from mkt_report import __main__ as cli
from mkt_report.alerts import stale_problem
from mkt_report.dashboard import build_dashboard
from mkt_report.db import SyncRun
from mkt_report.kpis import compute_kpis, sync_window
from mkt_report.report_pdf import build_pdf
from mkt_report.summary import claude_summary, template_summary, unverified_figures, write_summary
from mkt_report.sync import run_sync

from .conftest import make_sources

AS_OF = date(2026, 11, 2)


@pytest.fixture
def kpis(api, Session):
    start, end = sync_window(AS_OF, 9)
    run_sync(make_sources(api), Session, start, end)
    with Session() as s:
        return compute_kpis(s, AS_OF)


class FakeClaude:
    def __init__(self, text="", stop="end_turn", boom=None):
        self.text, self.stop, self.boom, self.calls = text, stop, boom, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if self.boom:
            raise self.boom
        return SimpleNamespace(stop_reason=self.stop, content=[SimpleNamespace(type="thinking", thinking="..."),
                                                                SimpleNamespace(type="text", text=self.text)])


def test_template_summary_has_the_three_lines(kpis):
    t = template_summary(kpis)
    lines = t.splitlines()
    assert lines[0].startswith("Week of 2026-10-26:") and f"{kpis['ga4']['this_week']['sessions']:,}" in lines[0]
    assert [line.split(":")[0] for line in lines[1:]] == ["Good", "Watch", "Next step"]
    assert "incomplete week from Google Search Console" in t
    assert unverified_figures(t, kpis) == []  # every number in it comes from the data


def test_figure_check_flags_invented_numbers(kpis):
    sessions = kpis["ga4"]["this_week"]["sessions"]
    good = f"Sessions were {sessions:,}, up {kpis['ga4']['change_pct']['sessions']}%."
    assert unverified_figures(good, kpis) == []
    assert unverified_figures(good + " Revenue was 98,765.43.", kpis) == ["98,765.43"]


def test_claude_summary_uses_numbers_only_prompt_and_reads_text_blocks(kpis):
    fake = FakeClaude("A steady week.\nGood: x\nWatch: y\nNext step: z")
    text, source = write_summary(kpis, client=fake)
    assert source == "claude" and text.startswith("A steady week.")
    call = fake.calls[0]
    assert call["model"] == "claude-sonnet-5-5"
    assert "ONLY the numbers" in call["system"] and '"report_week"' in call["messages"][0]["content"]


def test_ai_failure_falls_back_to_template(kpis):
    text, source = write_summary(kpis, client=FakeClaude(boom=RuntimeError("network down")))
    assert source == "template" and "AI summary unavailable: RuntimeError" in text
    with pytest.raises(RuntimeError, match="max_tokens"):
        claude_summary(kpis, "", client=FakeClaude("cut off", stop="max_tokens"))
    assert write_summary(kpis, use_ai=False)[1] == "template"


def test_pdf_has_page_numbers_on_every_page_and_date_on_page_one(kpis, tmp_path):
    when = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
    path = build_pdf(kpis, template_summary(kpis), "template", str(tmp_path / "r.pdf"), "https://www.example.com/", when)
    pages = PdfReader(path).pages
    assert len(pages) >= 2
    texts = [p.extract_text() for p in pages]
    for i, t in enumerate(texts, 1):
        assert f"Page {i} of {len(pages)}" in t
    assert "02 November 2026" in texts[0] and all("02 November 2026" not in t for t in texts[1:])
    joined = " ".join(texts)
    assert "Website (Google Analytics 4)" in joined and "Meta Ads" in joined and "Top queries this week" in joined


def test_pdf_and_dashboard_say_not_available_instead_of_zero(api, Session, tmp_path):
    start, end = sync_window(AS_OF, 9)
    run_sync(make_sources(api)[:1], Session, start, end)
    with Session() as s:
        k = compute_kpis(s, AS_OF)
    pdf = build_pdf(k, template_summary(k), "template", str(tmp_path / "r.pdf"), "site", datetime.now(timezone.utc))
    text = " ".join(p.extract_text() for p in PdfReader(pdf).pages)
    assert text.count("Not available for this week") == 2 and "not connected" in text
    html = open(build_dashboard(k, "s", str(tmp_path / "d.html"), "site", "now")).read()
    assert html.count("Not available for this week") == 2


def test_dashboard_escapes_text_and_embeds_data(kpis, tmp_path):
    html = open(build_dashboard(kpis, "<script>alert(1)</script>", str(tmp_path / "d.html"), "a&b", "now")).read()
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html and "a&amp;b" in html
    assert 'id="kpi-data"' in html and "<svg" in html
    assert "<script src" not in html and "<link" not in html  # self-contained, no external files


def test_stale_check(api, Session):
    now = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    with Session() as s:
        assert "no sync has ever run" in stale_problem(s, "ga4", 26, now)
        s.add(SyncRun(source="ga4", started_at=now - timedelta(hours=30), finished_at=now - timedelta(hours=30), status="ok", counts={}))
        s.commit()
        assert "30.0 hours ago" in stale_problem(s, "ga4", 26, now)
        assert stale_problem(s, "ga4", 48, now) is None
        s.add(SyncRun(source="ga4", started_at=now, status="failed", error="SourceError: 403", counts={}))
        s.add(SyncRun(source="meta", started_at=now, status="empty", counts={}))
        s.commit()
        assert "failed: SourceError: 403" in stale_problem(s, "ga4", 26, now)
        assert "returned no rows" in stale_problem(s, "meta", 26, now)


def test_cli_mock_run_end_to_end(tmp_path, capsys):
    out = tmp_path / "out"
    rc = cli.main(["run", "--mock", "--no-ai", "--db", f"sqlite:///{tmp_path / 'm.db'}", "--out", str(out)])
    assert rc == 0
    printed = capsys.readouterr().out
    assert "sync ga4: ok" in printed and "sync gsc: ok" in printed and "sync meta: ok" in printed
    assert (out / "marketing-report-2026-10-26.pdf").exists() and (out / "dashboard.html").exists() and (out / "kpis.json").exists()
    assert cli.main(["check", "--mock", "--db", f"sqlite:///{tmp_path / 'm.db'}", "--stale-hours", "1"]) == 0


def test_cli_without_any_configuration_alerts_and_fails(tmp_path, capsys, caplog):
    rc = cli.main(["sync", "--db", f"sqlite:///{tmp_path / 'x.db'}", "--out", str(tmp_path)])
    assert rc == 2
    assert "no sources configured" in caplog.text


def test_cli_missing_credentials_are_reported_per_source(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("GA4_PROPERTY_ID", "123")
    rc = cli.main(["sync", "--db", f"sqlite:///{tmp_path / 'x.db'}", "--out", str(tmp_path)])
    assert rc == 2 and "ga4 not configured" in caplog.text and "GOOGLE_APPLICATION_CREDENTIALS" in caplog.text
