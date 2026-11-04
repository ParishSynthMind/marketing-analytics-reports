# Marketing Analytics Reports (GA4, Search Console, Meta Ads)

A scheduled Python job that pulls website traffic from **Google Analytics 4** (Data API), Google search performance from **Search Console** (Search Analytics API) and paid social results from **Meta Ads** (Marketing API insights), all read-only, into SQLite or Postgres. It works out the weekly numbers a business owner actually asks about, has Claude write a short plain-English summary from those numbers only (with a check that every figure in it exists in the data), and produces a PDF report plus a one-file HTML dashboard. If a source fails, comes back empty or stops syncing, it sends an alert instead of going quiet.

![Dashboard built from the demo data](media/dashboard.png)

*The dashboard from the bundled demo data (a made-up desk shop, 13 weeks). The PDF carries the same numbers ([page 1](media/pdf-page1.png)). The summary at the top was written by Claude in a live run ([terminal capture](media/live-run.png)).*

## What it reports

| Source | KPIs | How it is counted |
|---|---|---|
| GA4 | Sessions, users, key events, engagement rate, key event rate, top 10 pages | Weeks run Monday to Sunday. Users come from a separate `isoYearIsoWeek` report, so a person who visits on three days counts once, not three times |
| Search Console | Clicks, impressions, CTR, average position, top 10 queries | Totals come from the site-level rows, because Google leaves rare (anonymised) queries out of the query table. Position is weighted by impressions |
| Meta Ads | Spend, CPC, conversions, cost per conversion, ROAS, per-campaign table | Conversions count only the action types you choose (`purchase`, `lead`...), so one sale reported as `purchase`, `omni_purchase` and a pixel purchase is not counted three times. ROAS only where a conversion value was reported |

Every KPI has a week-on-week change and an 8-week trend.

## How it works

```mermaid
flowchart LR
    S[Scheduler<br/>launchd / cron] --> A[mkt_report run]
    A -->|analytics.readonly| G4[(GA4 Data API<br/>runReport)]
    A -->|webmasters.readonly| SC[(Search Console<br/>searchAnalytics.query)]
    A -->|ads_read| M[(Meta Marketing API<br/>act_ID/insights)]
    G4 & SC & M --> D[(SQLite or Postgres<br/>via SQLAlchemy)]
    D --> K[Weekly KPI maths]
    K -->|numbers only| C[Claude summary<br/>+ figure check]
    K --> P[PDF report]
    K --> H[HTML dashboard]
    C --> P & H
    A -. failure, empty pull,<br/>or sync gone stale .-> X[Alert: email / webhook]
```

- **One adapter per source** (`mkt_report/sources/`), all behind the same small interface, sharing one HTTP transport with retries. Adding LinkedIn Ads or Google Ads means writing one more adapter.
- **Sync** (`python -m mkt_report sync`) pulls the last 9 weeks up to yesterday. The whole pull finishes before anything is written; then that window's rows are replaced in one transaction. A failed pull leaves the previous good data alone, and a re-pull corrects late-arriving numbers instead of duplicating them.
- **Report** (`python -m mkt_report report`) computes the KPIs, asks Claude for the summary and writes `marketing-report-<week>.pdf`, `dashboard.html` and `kpis.json`. `--email` sends them over SMTP.
- **Check** (`python -m mkt_report check`) exits non-zero and alerts if a source's last sync failed, came back empty, or the last good one is older than `STALE_AFTER_HOURS`.

## Design choices

- **Read-only credentials.** Google scopes are `analytics.readonly` and `webmasters.readonly`; Meta needs only `ads_read`. A reporting tool should not hold a write token.
- **Each API's quirks handled, not ignored.** GA4 sends every number as a string and leaves out `rows` entirely when there is no data; numbers are typed from `metricHeaders`. Search Console pages through `startRow` / `rowLimit` (max 25,000). Meta sends numbers as strings, paginates with `paging.next`, and reports rate limits as HTTP 400 with codes 4, 17, 32, 613 or 80000+, so those are retried while a bad token (code 190) fails at once.
- **Search Console's 2 to 3 day lag.** On a Monday the latest week only has 4 or 5 days of search data. Comparing that with a full week would show a fake drop, so the comparison uses the same weekdays of the week before, and the report says how many days it has.
- **Missing is never zero.** A source with no data for the week is shown as "Not available", with its sync status, never as 0.
- **The AI only writes words, never numbers.** Claude gets the KPI JSON, is told to use only those figures and not to guess what a "key event" is. Every number in its text is then checked against the data; anything not found is listed at the end of the PDF for a person to look at. If the AI call fails, a template summary is used and the report still goes out.
- **One portable dashboard file.** Inline SVG charts with hover tooltips and a "show as table" view, no CDN scripts, light and dark mode.

## Run it without any accounts

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

python -m mkt_report run --mock --no-ai     # fake APIs + demo data, template summary
export ANTHROPIC_API_KEY=...                # optional
python -m mkt_report report --mock          # same data, Claude writes the summary
open out/dashboard.html out/marketing-report-*.pdf
pytest                                      # 42 tests, every API and Claude mocked
```

`--mock` swaps in `mkt_report/mock_api.py`, an in-process stand-in that answers the three real endpoints in the shapes their documentation gives: GA4 `RunReportResponse` with typed `metricHeaders`, string values and `rowCount`; Search Console rows of `keys` / `clicks` / `impressions` / `ctr` / `position`, with anonymised queries counted in the totals but missing from the query rows and final data lagging 3 days; Meta insights with string numbers, `actions` / `action_values` / `purchase_roas` lists and cursor paging. Bearer tokens, property IDs, site URLs and ad account IDs are checked, so wrong ones fail the way the real APIs do. The data in `fixtures/demo_data.json` comes from `fixtures/make_fixtures.py`: invented pages, queries and campaigns for `www.example.com`.

## Run it on a real account

1. **Google Cloud:** in a Google Cloud project, enable the *Google Analytics Data API* and the *Google Search Console API*, create a service account and download its JSON key.
2. **GA4:** Admin > Property access management > add the service account's email as *Viewer*. Note the property ID (Admin > Property details).
3. **Search Console:** Settings > Users and permissions > add the same email with *Restricted* access. The site URL is either `https://www.example.com/` or `sc-domain:example.com`.
4. **Meta:** in Business Settings create a system user, give it the ad account, and generate a token with `ads_read`. Store it in your keychain: `security add-generic-password -s mkt-report-meta-token -a "$USER" -w`.
5. `cp .env.example .env`, fill in the IDs and the path to the JSON key, then `examples/run.sh run`. Only the sources with an ID set are pulled.
6. Schedule it with `examples/crontab.txt` (daily sync, Monday report, staleness check every 6 hours) or launchd.

For Postgres: `pip install "psycopg[binary]"` and set `DATABASE_URL=postgresql+psycopg://user:pass@host:5432/marketing`.

## Tests

`pytest` runs 42 tests with no network and no accounts:

- **Transport and auth:** retry on 429 and 5xx honouring `Retry-After`, a 60-second cap on waits, giving up with a clear error, no retry on 4xx, no token in `repr`, missing credentials explained. The service-account path runs the real `google-auth` code: a throwaway RSA key signs the JWT, only Google's token endpoint is faked, and the test checks the signature, the read-only scopes and that the token is reused rather than re-fetched.
- **Sources:** GA4 typing and offset paging, an empty GA4 response, wrong property (403) and bad token (401); Search Console paging, totals larger than the query rows, URL-encoding of `sc-domain:` properties; Meta string numbers, `paging.next`, one conversion family rather than three, lead campaigns, rate-limit 400s retried and a bad token not.
- **Sync and KPIs:** a re-sync does not duplicate, late numbers get corrected, one failing source does not stop the others and keeps its old data, an empty pull is marked `empty` not `ok`; KPIs checked against sums worked out by hand from the fixture, the like-for-like partial Search Console week, a missing source shown as unavailable, a single-week report.
- **Outputs:** the Claude call (model, numbers-only instruction) with a fake client, template fallback on failure, invented figures flagged, "Page N of M" on every PDF page with the date only on page 1, "Not available" instead of zeros, dashboard escaping and no external files, staleness rules, and the command line end to end including the no-configuration and missing-credentials alerts.

GitHub Actions runs lint and the suite on Python 3.9 and 3.12 with SQLite, again on a Postgres 16 service container, and does a full mock run each time. Set `TEST_DATABASE_URL` to run the tests on your own Postgres.

## Status

Tested live on 4 November 2026:

- 42 of 42 tests pass on SQLite (Python 3.9) and on a local Postgres 16 server; `ruff` is clean.
- Full mock run: GA4 63 days, 9 weekly-user rows and 756 page rows in 4 API calls; Search Console 60 days and 900 query rows in 3 calls; Meta 182 campaign-days in 2 calls. Same run on Postgres gave the same counts.
- **Claude summary, live** (`claude-sonnet-5-5`) on the demo data: it reported sessions at 4,056 (+12.2%), key events up 26.4% to 91, Meta ROAS down 27.9% to 3.98, flagged that Search Console only had 4 of 7 days, and suggested moving budget from the 2.66-ROAS prospecting campaign towards the 8.83-ROAS retargeting one. The figure check found every number in the data. ([terminal capture](media/live-run.png), [text](docs/live-run-2026-11-04.txt))
- Found on the way: the first live summary called GA4 key events "sign-ups", which the data does not say. The prompt now tells Claude not to rename them, and the second run kept the plain term.

**Not proven live yet:**

- **No real GA4, Search Console or Meta Ads account has been connected.** The freelance Google account used for this build has no GA4 property and no Search Console property yet, and no Meta ad account was used. Every API call in this README went to the built-in mock, which follows the shapes in each API's documentation. The real Google sign-in code (service-account JWT) is exercised in the tests against a fake token endpoint, not against Google.
- Real-world data quirks the mock may not have (sampling or thresholding on small GA4 properties, Meta attribution settings, currency conversion across accounts) have not been seen yet.

Honest limits:

- One GA4 property, one Search Console site and one Meta ad account per run; no multi-client roll-up yet.
- Amounts are shown without a currency symbol.
- Not built: Google Ads, LinkedIn Ads, TikTok Ads, Looker Studio or Google Sheets output.

## Stack

Python · Google Analytics Data API v1beta · Search Console API · Meta Marketing API (insights) · google-auth (service account) · SQLAlchemy (SQLite / Postgres) · Anthropic Claude API · ReportLab + Matplotlib (PDF) · inline SVG (dashboard) · pytest + responses · GitHub Actions

## Licence

MIT

---
Part of a small portfolio of tested automation/AI builds; see [ParishSynthMind](https://github.com/ParishSynthMind) for the others.
