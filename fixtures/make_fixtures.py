"""Generate fixtures/demo_data.json: 13 weeks of fake marketing data for a made-up site, www.example.com.

Deterministic (fixed seed), so the mock run and the tests always see the same numbers.
Nothing here is real: page paths, search queries and campaign names are invented.
  python fixtures/make_fixtures.py
"""
import json
import random
from datetime import date, timedelta
from pathlib import Path

ANCHOR = date(2026, 11, 2)            # the mock's "today" (a Monday)
START = date(2026, 8, 3)              # 13 full Monday-to-Sunday weeks before ANCHOR
GSC_LAG_DAYS = 3                      # Search Console's final data trails by 2-3 days, so the mock does too

PAGES = [("/", 0.30), ("/pricing", 0.12), ("/blog/how-to-choose-a-standing-desk", 0.10), ("/products/oak-desk", 0.09),
         ("/blog/home-office-lighting-guide", 0.07), ("/products/ergonomic-chair", 0.07), ("/contact", 0.05),
         ("/blog/cable-management-ideas", 0.05), ("/about", 0.04), ("/products/monitor-arm", 0.04),
         ("/checkout", 0.04), ("/blog/desk-height-calculator", 0.03)]
QUERIES = [("standing desk", 900, 6.2), ("oak standing desk", 300, 3.1), ("example desks", 160, 1.2),
           ("home office lighting", 700, 9.8), ("cable management ideas", 650, 11.4), ("ergonomic chair uk", 800, 14.5),
           ("desk height calculator", 420, 4.4), ("best monitor arm", 520, 17.9), ("standing desk benefits", 480, 12.3),
           ("small home office ideas", 610, 21.0), ("example desks discount code", 60, 1.4), ("oak desk", 240, 8.7),
           ("how high should a desk be", 380, 7.6), ("adjustable desk frame", 210, 15.2), ("quiet office chair", 150, 19.8)]
CAMPAIGNS = [("120200000000000101", "Autumn Sale - Prospecting", 42.0, "purchase"),
             ("120200000000000102", "Retargeting - Site Visitors", 21.0, "purchase"),
             ("120200000000000103", "Lead Magnet - Desk Setup Guide", 14.0, "lead")]


def main():
    rnd = random.Random(20261102)
    days = [START + timedelta(days=i) for i in range((ANCHOR - START).days)]
    ga4_daily, ga4_pages, gsc, meta = [], [], [], []
    weekly_users = {}
    for i, d in enumerate(days):
        trend = 1 + i * 0.004 + (0.08 if d >= ANCHOR - timedelta(days=7) else 0)  # gentle growth, a better last week
        base = 430 if d.weekday() < 5 else 290
        sessions = int(base * trend * rnd.uniform(0.9, 1.1))
        users = int(sessions * rnd.uniform(0.78, 0.84))
        ga4_daily.append({"date": d.isoformat(), "sessions": sessions, "totalUsers": users,
                          "newUsers": int(users * rnd.uniform(0.58, 0.66)),
                          "engagedSessions": int(sessions * rnd.uniform(0.55, 0.63)),
                          "keyEvents": int(sessions * rnd.uniform(0.018, 0.03))})
        y, w, _ = d.isocalendar()
        weekly_users[f"{y}{w:02d}"] = weekly_users.get(f"{y}{w:02d}", 0) + int(users * 0.8)  # repeat visitors de-duplicated
        for path, share in PAGES:
            views = int(sessions * share * 1.6 * rnd.uniform(0.8, 1.2))
            if views:
                ga4_pages.append({"date": d.isoformat(), "pagePath": path, "screenPageViews": views,
                                  "sessions": int(views * rnd.uniform(0.6, 0.8))})
        if d < ANCHOR - timedelta(days=GSC_LAG_DAYS):
            for q, impr, pos in QUERIES:
                im = int(impr / 7 * trend * rnd.uniform(0.75, 1.25))
                p = max(1.0, pos * rnd.uniform(0.9, 1.1) - i * 0.01)
                ctr = max(0.004, 0.32 / p ** 1.1) * rnd.uniform(0.8, 1.2)
                if im:
                    gsc.append({"date": d.isoformat(), "query": q, "clicks": int(im * ctr), "impressions": im,
                                "position": round(p, 2)})
        for cid, name, spend, action in CAMPAIGNS:
            if cid.endswith("103") and START + timedelta(days=21) <= d < START + timedelta(days=35):
                continue  # paused for two weeks: Meta simply returns no row for those days
            sp = round(spend * rnd.uniform(0.85, 1.15) * (1.1 if d >= ANCHOR - timedelta(days=7) else 1), 2)
            impressions = int(sp * rnd.uniform(70, 110))
            clicks = int(impressions * rnd.uniform(0.011, 0.019))
            conv = rnd.choice([0, 0, 1, 1, 2]) if action == "purchase" else rnd.choice([1, 2, 3, 4])
            value = round(conv * rnd.uniform(160, 340), 2) if action == "purchase" else 0
            meta.append({"date": d.isoformat(), "campaign_id": cid, "campaign_name": name, "spend": sp,
                         "impressions": impressions, "clicks": clicks, "link_clicks": int(clicks * 0.7),
                         "landing_page_views": int(clicks * 0.55), "action": action, "conversions": conv, "value": value})
    data = {"anchor": ANCHOR.isoformat(), "site": "https://www.example.com/", "ga4_property": "123456789",
            "meta_ad_account": "1234567890", "currency": "GBP", "time_zone": "Europe/London",
            "ga4_daily": ga4_daily, "ga4_pages": ga4_pages,
            "ga4_weekly_users": [{"isoYearIsoWeek": k, "totalUsers": v} for k, v in sorted(weekly_users.items())],
            "gsc": gsc, "meta": meta}
    out = Path(__file__).with_name("demo_data.json")
    out.write_text(json.dumps(data, indent=0))
    print(f"wrote {out}: {len(ga4_daily)} days, {len(ga4_pages)} page rows, {len(gsc)} query rows, {len(meta)} ad rows")


if __name__ == "__main__":
    main()
