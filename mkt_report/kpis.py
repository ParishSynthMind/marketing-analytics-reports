"""Weekly KPIs from the stored data. Weeks run Monday to Sunday; the report week is the last complete one.

A source with no stored rows for the report week is reported as unavailable (None), never as zero.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select

from .db import GA4Daily, GA4PageDaily, GA4WeeklyUsers, GSCDaily, GSCQueryDaily, MetaDaily, SyncRun

LABELS = {"ga4": "Google Analytics 4", "gsc": "Google Search Console", "meta": "Meta Ads"}


def report_week(as_of: date) -> date:
    """Monday of the last complete Monday-to-Sunday week before as_of."""
    this_monday = as_of - timedelta(days=as_of.weekday())
    return this_monday - timedelta(days=7)


def sync_window(as_of: date, weeks: int) -> Tuple[date, date]:
    """What to pull: from the Monday `weeks` weeks back up to yesterday (so the current week fills in too)."""
    return report_week(as_of) - timedelta(days=7 * (weeks - 1)), as_of - timedelta(days=1)


def iso_week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}{w:02d}"


def pct_change(cur, prev) -> Optional[float]:
    if cur is None or prev is None or not prev:
        return None
    return round((cur - prev) / prev * 100, 1)


def _r(v, n=2):
    return None if v is None else round(v, n)


def _div(a, b, scale=1.0, n=2):
    return round(a / b * scale, n) if b else None


def _week_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _like_for_like(rows, weeks: List[date]):
    """Previous-week rows limited to the weekdays the report week actually has.

    Search Console's final data trails by 2-3 days, so a Monday report can hold only 4-5 days of the
    latest week. Comparing those with a full previous week would show a fake drop, so the comparison
    uses the same weekdays of the week before. Returns (rows_for_prev, compared_days or None if full).
    """
    if len(weeks) < 2:
        return [], None
    cur_days = {r.day.weekday() for r in rows if _week_of(r.day) == weeks[-1]}
    prev = [r for r in rows if _week_of(r.day) == weeks[-2]]
    if not cur_days or len(cur_days) >= 7:
        return prev, None
    return [r for r in prev if r.day.weekday() in cur_days], len(cur_days)


def _source_status(s, source: str) -> Dict:
    run = s.scalars(select(SyncRun).where(SyncRun.source == source).order_by(SyncRun.id.desc()).limit(1)).first()
    if run is None:
        return {"connected": False, "last_sync": None, "last_status": None}
    return {"connected": True, "last_sync": run.started_at.strftime("%Y-%m-%d %H:%M UTC"), "last_status": run.status,
            "last_error": run.error}


# ---------- GA4 ----------

def _ga4(s, weeks: List[date]) -> Optional[Dict]:
    lo, hi = weeks[0], weeks[-1] + timedelta(days=6)
    rows = s.scalars(select(GA4Daily).where(GA4Daily.day >= lo, GA4Daily.day <= hi)).all()
    if not rows:
        return None
    users = {u.iso_week: u.total_users for u in s.scalars(select(GA4WeeklyUsers)).all()}
    def build(rs):
        agg = defaultdict(lambda: {"sessions": 0, "engaged_sessions": 0, "new_users": 0, "key_events": 0.0, "days": 0})
        for r in rs:
            a = agg[_week_of(r.day)]
            a["sessions"] += r.sessions
            a["engaged_sessions"] += r.engaged_sessions
            a["new_users"] += r.new_users
            a["key_events"] += r.key_events
            a["days"] += 1
        return agg

    agg = build(rows)

    def week(w: date, src=None) -> Optional[Dict]:
        src = agg if src is None else src
        if w not in src:
            return None
        a = src[w]
        return {"sessions": a["sessions"], "users": users.get(iso_week(w)), "new_users": a["new_users"],
                "engagement_rate_pct": _div(a["engaged_sessions"], a["sessions"], 100, 1),
                "key_events": _r(a["key_events"], 1), "key_event_rate_pct": _div(a["key_events"], a["sessions"], 100, 2),
                "days_with_data": a["days"]}

    cur, prev = week(weeks[-1]), week(weeks[-2]) if len(weeks) > 1 else None
    if cur is None:
        return None
    prev_rows, compared = _like_for_like(rows, weeks)
    base = week(weeks[-2], build(prev_rows)) if compared else prev
    pages_cur = defaultdict(int)
    pages_prev = defaultdict(int)
    prev_days = {r.day.weekday() for r in prev_rows}
    for p in s.scalars(select(GA4PageDaily).where(GA4PageDaily.day >= (weeks[-2] if len(weeks) > 1 else weeks[-1]),
                                                  GA4PageDaily.day <= hi)).all():
        if _week_of(p.day) == weeks[-1]:
            pages_cur[p.page_path] += p.views
        elif not compared or p.day.weekday() in prev_days:
            pages_prev[p.page_path] += p.views
    top = sorted(pages_cur.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    return {
        "this_week": cur, "prev_week": prev,
        # GA4 de-duplicates users per whole week, so users can't be compared like for like on a partial week
        "change_pct": {k: None if (compared and k == "users") else pct_change(cur[k], (base or {}).get(k))
                       for k in ("sessions", "users", "key_events")},
        "compared_days": compared,
        "weekly": [{"week": w.isoformat(), "sessions": (week(w) or {}).get("sessions"), "users": users.get(iso_week(w)),
                    "key_events": (week(w) or {}).get("key_events")} for w in weeks],
        "top_pages": [{"page": p, "views": v, "prev_views": pages_prev.get(p, 0),
                       "change_pct": pct_change(v, pages_prev.get(p))} for p, v in top],
    }


# ---------- Search Console ----------

def _gsc(s, weeks: List[date]) -> Optional[Dict]:
    lo, hi = weeks[0], weeks[-1] + timedelta(days=6)
    rows = s.scalars(select(GSCDaily).where(GSCDaily.day >= lo, GSCDaily.day <= hi)).all()
    if not rows:
        return None
    def build(rs):
        agg = defaultdict(lambda: {"clicks": 0.0, "impressions": 0.0, "pos_x_impr": 0.0, "days": 0})
        for r in rs:
            a = agg[_week_of(r.day)]
            a["clicks"] += r.clicks
            a["impressions"] += r.impressions
            a["pos_x_impr"] += r.position * r.impressions  # weight each day's average position by its impressions
            a["days"] += 1
        return agg

    agg = build(rows)

    def week(w: date, src=None) -> Optional[Dict]:
        src = agg if src is None else src
        if w not in src:
            return None
        a = src[w]
        return {"clicks": int(round(a["clicks"])), "impressions": int(round(a["impressions"])),
                "ctr_pct": _div(a["clicks"], a["impressions"], 100, 2),
                "avg_position": _div(a["pos_x_impr"], a["impressions"], 1, 1), "days_with_data": a["days"]}

    cur, prev = week(weeks[-1]), week(weeks[-2]) if len(weeks) > 1 else None
    if cur is None:
        return None
    prev_rows, compared = _like_for_like(rows, weeks)
    base = week(weeks[-2], build(prev_rows)) if compared else prev
    prev_days = {r.day.weekday() for r in prev_rows}
    q = defaultdict(lambda: {"clicks": 0.0, "impressions": 0.0, "pos_x_impr": 0.0})
    q_prev = defaultdict(float)
    for r in s.scalars(select(GSCQueryDaily).where(GSCQueryDaily.day >= (weeks[-2] if len(weeks) > 1 else weeks[-1]),
                                                   GSCQueryDaily.day <= hi)).all():
        if _week_of(r.day) == weeks[-1]:
            a = q[r.query]
            a["clicks"] += r.clicks
            a["impressions"] += r.impressions
            a["pos_x_impr"] += r.position * r.impressions
        elif not compared or r.day.weekday() in prev_days:
            q_prev[r.query] += r.clicks
    top = sorted(q.items(), key=lambda kv: (-kv[1]["clicks"], -kv[1]["impressions"], kv[0]))[:10]
    pos_change = (round(cur["avg_position"] - base["avg_position"], 1)
                  if base and cur["avg_position"] is not None and base["avg_position"] is not None else None)
    return {
        "this_week": cur, "prev_week": prev,
        "change_pct": {k: pct_change(cur[k], (base or {}).get(k)) for k in ("clicks", "impressions")},
        "compared_days": compared,
        "ctr_change_points": (round(cur["ctr_pct"] - base["ctr_pct"], 2)
                              if base and cur["ctr_pct"] is not None and base["ctr_pct"] is not None else None),
        "position_change": pos_change,  # negative = moved up the results page (better)
        "weekly": [{"week": w.isoformat(), "clicks": (week(w) or {}).get("clicks"),
                    "impressions": (week(w) or {}).get("impressions")} for w in weeks],
        "top_queries": [{"query": k, "clicks": int(round(a["clicks"])), "impressions": int(round(a["impressions"])),
                         "ctr_pct": _div(a["clicks"], a["impressions"], 100, 1),
                         "avg_position": _div(a["pos_x_impr"], a["impressions"], 1, 1),
                         "prev_clicks": int(round(q_prev.get(k, 0)))} for k, a in top],
    }


# ---------- Meta Ads ----------

def _meta(s, weeks: List[date]) -> Optional[Dict]:
    lo, hi = weeks[0], weeks[-1] + timedelta(days=6)
    rows = s.scalars(select(MetaDaily).where(MetaDaily.day >= lo, MetaDaily.day <= hi)).all()
    if not rows:
        return None
    agg = defaultdict(lambda: {"spend": 0.0, "impressions": 0, "clicks": 0, "conversions": 0.0, "value": 0.0, "days": set()})
    camps = defaultdict(lambda: {"name": None, "spend": 0.0, "clicks": 0, "conversions": 0.0, "value": 0.0})
    for r in rows:
        w = _week_of(r.day)
        a = agg[w]
        a["spend"] += r.spend
        a["impressions"] += r.impressions
        a["clicks"] += r.clicks
        a["conversions"] += r.conversions
        a["value"] += r.conversion_value
        a["days"].add(r.day)
        if w == weeks[-1]:
            c = camps[r.campaign_id]
            c["name"] = r.campaign_name or r.campaign_id
            c["spend"] += r.spend
            c["clicks"] += r.clicks
            c["conversions"] += r.conversions
            c["value"] += r.conversion_value

    def week(w: date) -> Optional[Dict]:
        if w not in agg:
            return None
        a = agg[w]
        return {"spend": round(a["spend"], 2), "impressions": a["impressions"], "clicks": a["clicks"],
                "ctr_pct": _div(a["clicks"], a["impressions"], 100, 2), "cpc": _div(a["spend"], a["clicks"]),
                "conversions": _r(a["conversions"], 1), "conversion_value": round(a["value"], 2),
                "cost_per_conversion": _div(a["spend"], a["conversions"]),
                # ROAS only where conversion value was actually reported
                "roas": _div(a["value"], a["spend"]) if a["value"] > 0 else None,
                "days_with_data": len(a["days"])}

    cur, prev = week(weeks[-1]), week(weeks[-2]) if len(weeks) > 1 else None
    if cur is None:
        return None
    return {
        "this_week": cur, "prev_week": prev,
        "change_pct": {k: pct_change(cur[k], (prev or {}).get(k)) for k in ("spend", "clicks", "cpc", "conversions", "roas")},
        "weekly": [{"week": w.isoformat(), "spend": (week(w) or {}).get("spend"),
                    "conversions": (week(w) or {}).get("conversions")} for w in weeks],
        "campaigns": [{"campaign": c["name"], "spend": round(c["spend"], 2), "clicks": c["clicks"],
                       "conversions": round(c["conversions"], 1), "cpc": _div(c["spend"], c["clicks"]),
                       "roas": _div(c["value"], c["spend"]) if c["value"] > 0 else None}
                      for c in sorted(camps.values(), key=lambda c: -c["spend"])],
    }


def compute_kpis(s, as_of: date, weeks: int = 8) -> Dict:
    first = report_week(as_of) - timedelta(days=7 * (weeks - 1))
    week_list = [first + timedelta(days=7 * i) for i in range(weeks)]
    ga4, gsc, meta = _ga4(s, week_list), _gsc(s, week_list), _meta(s, week_list)
    status = {k: _source_status(s, k) for k in LABELS}
    for k, block in (("ga4", ga4), ("gsc", gsc), ("meta", meta)):
        status[k]["label"] = LABELS[k]
        status[k]["has_report_week"] = block is not None
        days = block["this_week"]["days_with_data"] if block else 0
        status[k]["report_week_days"] = days
    wk = week_list[-1]
    return {
        "as_of": as_of.isoformat(),
        "report_week": {"start": wk.isoformat(), "end": (wk + timedelta(days=6)).isoformat()},
        "previous_week": {"start": (wk - timedelta(days=7)).isoformat(), "end": (wk - timedelta(days=1)).isoformat()},
        "weeks": weeks,
        "sources": status,
        "ga4": ga4, "gsc": gsc, "meta": meta,
    }
