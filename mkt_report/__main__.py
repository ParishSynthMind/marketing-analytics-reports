"""Command line.

  python -m mkt_report sync     pull GA4, Search Console and Meta Ads into the database
  python -m mkt_report report   compute weekly KPIs, write PDF + HTML dashboard (+ email if configured)
  python -m mkt_report run      sync then report (what the scheduler calls)
  python -m mkt_report check    alert if any source's syncing has stopped, failed or come back empty

Add --mock to use the built-in fake APIs and demo data (no accounts or tokens needed),
and --no-ai to skip Claude and use the template summary.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List

from . import alerts
from .auth import google_auth_header, meta_auth_header
from .dashboard import build_dashboard
from .db import make_session_factory
from .http import Transport
from .kpis import compute_kpis, sync_window
from .report_pdf import build_pdf
from .sources import GA4Source, GSCSource, MetaAdsSource
from .sources.meta import meta_should_retry
from .summary import unverified_figures, write_summary
from .sync import run_sync

log = logging.getLogger("mkt_report")
ALL_SOURCES = ("ga4", "gsc", "meta")


def load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def wanted_sources(args) -> List[str]:
    if args.sources:
        names = [s.strip() for s in args.sources.split(",") if s.strip()]
        bad = [n for n in names if n not in ALL_SOURCES]
        if bad:
            raise SystemExit(f"unknown source(s): {bad}; choose from {ALL_SOURCES}")
        return names
    if args.mock:
        return list(ALL_SOURCES)
    env = {"ga4": "GA4_PROPERTY_ID", "gsc": "GSC_SITE_URL", "meta": "META_AD_ACCOUNT_ID"}
    return [n for n in ALL_SOURCES if os.environ.get(env[n])]


def build_sources(args, names: List[str]):
    """Return the adapters to sync. Building one can fail (missing credentials); that is reported, not hidden."""
    sources, problems = [], []
    if args.mock:
        from .mock_api import GOOGLE_TOKEN, META_TOKEN, MockAPISession
        sess = MockAPISession()
        d = sess.data
        google = lambda: {"Authorization": f"Bearer {GOOGLE_TOKEN}"}  # noqa: E731
        meta = lambda: {"Authorization": f"Bearer {META_TOKEN}"}  # noqa: E731
        mk = {"ga4": lambda: GA4Source(Transport("ga4", google, sess, sleep=lambda s: None), d["ga4_property"], page_size=400),
              "gsc": lambda: GSCSource(Transport("gsc", google, sess, sleep=lambda s: None), d["site"], page_size=500),
              "meta": lambda: MetaAdsSource(Transport("meta", meta, sess, sleep=lambda s: None, also_retry=meta_should_retry),
                                            d["meta_ad_account"], page_size=100,
                                            conversion_actions=("purchase", "lead"))}  # the demo account runs both kinds
        return [mk[n]() for n in names], problems, sess
    for n in names:
        try:
            if n == "ga4":
                sources.append(GA4Source(Transport("ga4", google_auth_header()), os.environ.get("GA4_PROPERTY_ID", "")))
            elif n == "gsc":
                sources.append(GSCSource(Transport("gsc", google_auth_header()), os.environ.get("GSC_SITE_URL", ""),
                                         data_state=os.environ.get("GSC_DATA_STATE", "final")))
            elif n == "meta":
                acts = os.environ.get("META_CONVERSION_ACTIONS", "purchase").split(",")
                sources.append(MetaAdsSource(Transport("meta", meta_auth_header(), also_retry=meta_should_retry),
                                             os.environ.get("META_AD_ACCOUNT_ID", ""),
                                             version=os.environ.get("META_API_VERSION", "v26.0"), conversion_actions=acts))
        except Exception as exc:
            problems.append((n, exc))
    return sources, problems, None


def as_of_for(args, mock_session) -> date:
    if args.as_of:
        return date.fromisoformat(args.as_of)
    if mock_session is not None:  # demo data has its own "today"
        return mock_session.anchor
    return datetime.now(timezone.utc).date()


def do_sync(args, Session, sources, problems, as_of) -> bool:
    start, end = sync_window(as_of, args.weeks + 1)  # one extra week so the first trend week has a comparison
    ok = True
    for name, exc in problems:
        alerts.alert(f"Marketing report: {name} not configured", f"{type(exc).__name__}: {exc}")
        ok = False
    if not sources and not problems:
        alerts.alert("Marketing report: no sources configured",
                     "Set GA4_PROPERTY_ID, GSC_SITE_URL and/or META_AD_ACCOUNT_ID (or pass --mock).")
        return False
    runs, failures = run_sync(sources, Session, start, end)
    for src, exc in failures:
        alerts.alert(f"Marketing report: {src.label} sync FAILED", f"{type(exc).__name__}: {exc}")
        ok = False
    for run in runs:
        if run.status == "empty":
            hint = " (a Meta ad account with no spend in the window also returns no rows)" if run.source == "meta" else ""
            alerts.alert(f"Marketing report: {run.source} returned no data",
                         f"Run {run.id} for {start} to {end} finished but every report came back empty{hint}. "
                         "Check the property / site / ad account ID and the access granted to the credentials.")
            ok = False
        print(f"sync {run.source}: {run.status}, {json.dumps(run.counts)}, {run.api_calls} API calls")
    return ok


def do_report(args, Session, as_of, site: str):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with Session() as s:
        k = compute_kpis(s, as_of, weeks=args.weeks)
    if not any(k[n] for n in ALL_SOURCES):
        alerts.alert("Marketing report: no data for the report week",
                     f"No source has stored data for {k['report_week']['start']} to {k['report_week']['end']}; "
                     "the report was still written so the gap is visible.")
    summary, source = write_summary(k, use_ai=not args.no_ai)
    flags = unverified_figures(summary, k) if source == "claude" else []
    stamp = k["report_week"]["start"]
    (out / "kpis.json").write_text(json.dumps(k, indent=1, default=str))
    (out / "summary.txt").write_text(summary + "\n")
    notes = []
    if flags:
        notes.append("Figures in the summary that are not copied directly from the data (worth a glance): " + ", ".join(flags) + ".")
    pdf = build_pdf(k, summary, source, str(out / f"marketing-report-{stamp}.pdf"), site, datetime.now(timezone.utc), notes)
    html = build_dashboard(k, summary, str(out / "dashboard.html"), site, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    print(f"summary ({source}):\n{summary}\n")
    print(f"figure check: {'all figures found in the data' if not flags else 'not found verbatim in the data: ' + str(flags)}"
          if source == "claude" else "figure check: n/a (template summary)")
    print(f"wrote {pdf}\nwrote {html}\nwrote {out / 'kpis.json'}")
    if args.email:
        sent = alerts.send_email(f"Weekly marketing report, week of {stamp}", summary, [pdf, html])
        print("emailed report" if sent else "email not configured, skipped")
    return k, summary, source, flags


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mkt_report", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["sync", "report", "run", "check"])
    ap.add_argument("--mock", action="store_true", help="use the built-in fake APIs and demo data")
    ap.add_argument("--db", help="SQLAlchemy URL (default: $DATABASE_URL or sqlite:///marketing.db)")
    ap.add_argument("--out", default="out", help="output folder for the PDF, dashboard and kpis.json")
    ap.add_argument("--as-of", help="report date YYYY-MM-DD; the report covers the last full week before it")
    ap.add_argument("--weeks", type=int, default=8, help="weeks of trend to show (default 8)")
    ap.add_argument("--sources", help="comma list of ga4,gsc,meta (default: every source with an ID configured)")
    ap.add_argument("--no-ai", action="store_true", help="skip Claude, use the template summary")
    ap.add_argument("--email", action="store_true", help="email the report if SMTP_* is configured")
    ap.add_argument("--stale-hours", type=float, default=None)
    args = ap.parse_args(argv)

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for noisy in ("matplotlib", "httpx", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    db_url = args.db or os.environ.get("DATABASE_URL") or ("sqlite:///marketing_mock.db" if args.mock else "sqlite:///marketing.db")
    Session = make_session_factory(db_url)
    names = wanted_sources(args)

    if args.command == "check":
        hours = args.stale_hours or float(os.environ.get("STALE_AFTER_HOURS", "26"))
        problems = []
        with Session() as s:
            for n in names or ALL_SOURCES:
                p = alerts.stale_problem(s, n, hours)
                if p:
                    problems.append(p)
        if problems:
            alerts.alert("Marketing report: syncing has a problem", "\n".join(problems))
            print("PROBLEM:\n" + "\n".join(problems))
            return 1
        print("sync healthy for: " + ", ".join(names or ALL_SOURCES))
        return 0

    sources, problems, mock_session = build_sources(args, names)
    as_of = as_of_for(args, mock_session)
    default_site = mock_session.data["site"] if mock_session else os.environ.get("GSC_SITE_URL", "Your site")
    site = os.environ.get("REPORT_SITE_NAME") or default_site
    ok = True
    if args.command in ("sync", "run"):
        ok = do_sync(args, Session, sources, problems, as_of)
    if args.command in ("report", "run"):
        try:
            do_report(args, Session, as_of, site)
        except Exception as exc:
            alerts.alert("Marketing report FAILED", f"{type(exc).__name__}: {exc}")
            raise
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
