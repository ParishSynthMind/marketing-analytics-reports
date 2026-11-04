"""One static HTML file: KPI tiles plus inline SVG charts per source. No external scripts, works offline and in email attachments."""
from __future__ import annotations

import html
import json
from typing import Dict, List

CSS = """
.viz-root{color-scheme:light;--surface-0:#f4f3f0;--surface-1:#fcfcfb;--text-primary:#0b0b0b;--text-secondary:#52514e;
--text-muted:#7a7974;--grid:#e4e3df;--series-1:#2a78d6;--series-1-soft:#a9c8ef;--series-1-hover:#1c5cab}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;--surface-0:#111110;
--surface-1:#1a1a19;--text-primary:#f0efec;--text-secondary:#c3c2b7;--text-muted:#94938b;--grid:#2e2d2b;--series-1:#3987e5;--series-1-soft:#2c4f7c;--series-1-hover:#6da7ec}}
:root[data-theme="dark"] .viz-root{color-scheme:dark;--surface-0:#111110;--surface-1:#1a1a19;--text-primary:#f0efec;
--text-secondary:#c3c2b7;--text-muted:#94938b;--grid:#2e2d2b;--series-1:#3987e5;--series-1-soft:#2c4f7c;--series-1-hover:#6da7ec}
*{box-sizing:border-box}body{margin:0}
.viz-root{background:var(--surface-0);color:var(--text-primary);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
min-height:100vh;padding:28px}
.wrap{max-width:1080px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}h2{font-size:15px;margin:0 0 2px}.sub{color:var(--text-secondary);font-size:13px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:20px 0}
.tile,.card{background:var(--surface-1);border:1px solid var(--grid);border-radius:10px;padding:14px 16px}
.tile .k{color:var(--text-secondary);font-size:12px}.tile .v{font-size:26px;font-weight:600;margin:2px 0}
.tile .s{color:var(--text-muted);font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(460px,1fr));gap:12px}
.summary{white-space:pre-line;margin:8px 0 0}
svg text{fill:var(--text-secondary);font-size:11px}svg .val{fill:var(--text-primary)}
svg .bar{fill:var(--series-1-soft)}svg .bar.cur,svg .rowbar{fill:var(--series-1)}svg .hit:hover+.bar,svg .bar:hover{fill:var(--series-1-hover)}
svg .gl{stroke:var(--grid);stroke-width:1}
details{margin-top:8px;color:var(--text-secondary);font-size:12px}table{border-collapse:collapse;margin-top:6px}
td,th{padding:3px 10px 3px 0;text-align:right;border-bottom:1px solid var(--grid)}td:first-child,th:first-child{text-align:left}
#tip{position:fixed;pointer-events:none;background:var(--surface-1);color:var(--text-primary);border:1px solid var(--grid);
border-radius:6px;padding:6px 9px;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.15);display:none}
.foot{color:var(--text-muted);font-size:12px;margin-top:18px}
.up{color:#1d7a46}.down{color:#b3261e}@media (prefers-color-scheme:dark){.up{color:#5cc38a}.down{color:#f08a80}}
h3{font-size:17px;margin:26px 0 0}.na{color:var(--text-secondary)}
.status td,.status th{text-align:left}
"""

JS = """
const tip=document.getElementById('tip');
document.querySelectorAll('[data-tip]').forEach(el=>{
 el.addEventListener('mousemove',e=>{tip.textContent=el.dataset.tip;tip.style.display='block';
  tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY+12)+'px'});
 el.addEventListener('mouseleave',()=>{tip.style.display='none'});});
"""


def _e(s) -> str:
    return html.escape(str(s))


def _nice_max(v: float) -> float:
    if v <= 0:
        return 1
    import math
    mag = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * mag:
            return m * mag
    return 10 * mag


def svg_columns(labels: List[str], values: List[float], unit: str, w: int = 480, h: int = 220) -> str:
    left, right, top, bottom = 34, 8, 12, 28
    pw, ph = w - left - right, h - top - bottom
    vmax = _nice_max(max(values) if values else 1)
    step = pw / max(len(values), 1)
    bw = min(step * 0.6, 40)
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img">']
    for i in range(5):
        y = top + ph - ph * i / 4
        out.append(f'<line class="gl" x1="{left}" x2="{w-right}" y1="{y:.1f}" y2="{y:.1f}"/>'
                   f'<text x="{left-6}" y="{y+4:.1f}" text-anchor="end">{vmax*i/4:g}</text>')
    for i, (lab, v) in enumerate(zip(labels, values)):
        x = left + step * i + (step - bw) / 2
        bh = ph * v / vmax
        y = top + ph - bh
        r = min(4, bw / 2, bh)
        path = (f"M{x:.1f},{top+ph:.1f} V{y+r:.1f} Q{x:.1f},{y:.1f} {x+r:.1f},{y:.1f} H{x+bw-r:.1f} "
                f"Q{x+bw:.1f},{y:.1f} {x+bw:.1f},{y+r:.1f} V{top+ph:.1f} Z") if bh > 0 else ""
        tipt = _e(f"{lab}: {v:g} {unit}")
        out.append(f'<rect class="hit" x="{left+step*i:.1f}" y="{top}" width="{step:.1f}" height="{ph}" fill="transparent" data-tip="{tipt}"/>')
        if path:
            cls = "bar cur" if i == len(values) - 1 else "bar"  # the report week stands out
            out.append(f'<path class="{cls}" d="{path}" data-tip="{tipt}"/>')
        out.append(f'<text x="{x+bw/2:.1f}" y="{h-10}" text-anchor="middle">{_e(lab)}</text>')
    if values:  # label only the latest value, not every bar
        i = len(values) - 1
        x = left + step * i + step / 2
        out.append(f'<text class="val" x="{x:.1f}" y="{top+ph-ph*values[-1]/vmax-5:.1f}" text-anchor="middle">{values[-1]:,.0f}</text>')
    out.append("</svg>")
    return "".join(out)


def svg_rows(labels: List[str], values: List[float], fmt=lambda v: f"{v:g}", w: int = 480) -> str:
    row_h, left, right = 26, 190, 64
    h = row_h * len(labels) + 8
    vmax = max(values) if values and max(values) > 0 else 1
    pw = w - left - right
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img">']
    for i, (lab, v) in enumerate(zip(labels, values)):
        y = 4 + i * row_h
        bw = pw * v / vmax
        bh = row_h - 10
        r = min(4, bw, bh / 2)
        tipt = _e(f"{lab}: {fmt(v)}")
        short = lab if len(lab) <= 28 else lab[:27] + "..."
        out.append(f'<text x="{left-8}" y="{y+bh/2+4:.1f}" text-anchor="end">{_e(short)}</text>')
        out.append(f'<rect class="hit" x="0" y="{y}" width="{w}" height="{row_h}" fill="transparent" data-tip="{tipt}"/>')
        if bw > 0:
            out.append(f'<path class="bar rowbar" data-tip="{tipt}" d="M{left},{y} H{left+bw-r:.1f} Q{left+bw:.1f},{y} {left+bw:.1f},{y+r:.1f} '
                       f'V{y+bh-r:.1f} Q{left+bw:.1f},{y+bh:.1f} {left+bw-r:.1f},{y+bh:.1f} H{left} Z"/>')
        out.append(f'<text class="val" x="{left+bw+6:.1f}" y="{y+bh/2+4:.1f}">{_e(fmt(v))}</text>')
    out.append("</svg>")
    return "".join(out)


def _table(head: List[str], rows: List[List]) -> str:
    th = "".join(f"<th>{_e(x)}</th>" for x in head)
    trs = "".join("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<details><summary>Show as table</summary><table><tr>{th}</tr>{trs}</table></details>"


def _na(v, suffix=""):
    if v is None:
        return "n/a"
    return f"{v:,}{suffix}" if isinstance(v, int) else f"{v:g}{suffix}"


def _chg(v, higher_is_better=True, unit="%") -> str:
    if v is None:
        return "no comparison"
    if v == 0:
        return "no change"
    cls = "up" if (v > 0) == higher_is_better else "down"
    return f'<span class="{cls}">{"+" if v > 0 else ""}{v:g}{unit}</span> vs previous week'


def _tiles(items) -> str:
    return '<div class="tiles">' + "".join(
        f'<div class="tile"><div class="k">{_e(a)}</div><div class="v">{_e(b)}</div><div class="s">{c}</div></div>'
        for a, b, c in items) + "</div>"


def _cards(cards) -> str:
    return '<div class="grid">' + "".join(f'<div class="card"><h2>{_e(t)}</h2><div class="sub">{_e(s)}</div>{svg}{tbl}</div>'
                                          for t, s, svg, tbl in cards) + "</div>"


def _z(values):
    return [v if v is not None else 0 for v in values]


def build_dashboard(k: Dict, summary: str, path: str, site: str, generated: str) -> str:
    rw = k["report_week"]
    money = lambda v: "n/a" if v is None else f"{v:,.2f}"  # noqa: E731
    na = '<p class="na">Not available for this week (see Data sources). Never shown as zero.</p>'
    parts = []

    g = k["ga4"]
    parts.append("<h3>Website (Google Analytics 4)</h3>")
    if g:
        t, c = g["this_week"], g["change_pct"]
        parts.append(_tiles([("Sessions", f"{t['sessions']:,}", _chg(c["sessions"])),
                             ("Users", _na(t["users"]), _chg(c["users"])),
                             ("Key events", _na(t["key_events"]), _chg(c["key_events"])),
                             ("Engagement rate", _na(t["engagement_rate_pct"], "%"), f"key event rate {_na(t['key_event_rate_pct'], '%')}")]))
        wk = g["weekly"]
        parts.append(_cards([
            ("Sessions per week", "Week starting Monday; darker bar is the report week",
             svg_columns([w["week"][5:] for w in wk], _z([w["sessions"] for w in wk]), "sessions"),
             _table(["Week", "Sessions", "Users", "Key events"], [[w["week"], _na(w["sessions"]), _na(w["users"]), _na(w["key_events"])] for w in wk])),
            ("Top pages this week", "Page views",
             svg_rows([p["page"] for p in g["top_pages"]], [p["views"] for p in g["top_pages"]], fmt=lambda v: f"{v:,.0f}"),
             _table(["Page", "Views", "Previous week", "Change"], [[p["page"], p["views"], p["prev_views"], _na(p["change_pct"], "%")] for p in g["top_pages"]])),
        ]))
    else:
        parts.append(na)

    s = k["gsc"]
    parts.append("<h3>Google search (Search Console)</h3>")
    if s:
        t, c = s["this_week"], s["change_pct"]
        parts.append(_tiles([("Clicks", f"{t['clicks']:,}", _chg(c["clicks"])),
                             ("Impressions", f"{t['impressions']:,}", _chg(c["impressions"])),
                             ("Click-through rate", _na(t["ctr_pct"], "%"), _chg(s["ctr_change_points"], unit=" pts")),
                             ("Average position", _na(t["avg_position"]), _chg(s["position_change"], higher_is_better=False, unit="") +
                              " (lower is better)")]))
        wk = s["weekly"]
        parts.append(_cards([
            ("Clicks from Google per week", "Search Console data trails by 2 to 3 days" + (
                f"; the report week has only {t['days_with_data']} of 7 days so far" if t["days_with_data"] < 7 else ""),
             svg_columns([w["week"][5:] for w in wk], _z([w["clicks"] for w in wk]), "clicks"),
             _table(["Week", "Clicks", "Impressions"], [[w["week"], _na(w["clicks"]), _na(w["impressions"])] for w in wk])),
            ("Top search queries this week", "Clicks",
             svg_rows([q["query"] for q in s["top_queries"]], [q["clicks"] for q in s["top_queries"]]),
             _table(["Query", "Clicks", "Impressions", "CTR", "Position", "Prev. clicks"],
                    [[q["query"], q["clicks"], q["impressions"], _na(q["ctr_pct"], "%"), _na(q["avg_position"]), q["prev_clicks"]]
                     for q in s["top_queries"]])),
        ]))
    else:
        parts.append(na)

    m = k["meta"]
    parts.append("<h3>Paid social (Meta Ads)</h3>")
    if m:
        t, c = m["this_week"], m["change_pct"]
        parts.append(_tiles([("Spend", money(t["spend"]), _chg(c["spend"], higher_is_better=False)),
                             ("Cost per click", money(t["cpc"]), _chg(c["cpc"], higher_is_better=False)),
                             ("Conversions", _na(t["conversions"]), f"cost per conversion {money(t['cost_per_conversion'])}"),
                             ("Return on ad spend", _na(t["roas"]), _chg(c["roas"]) if t["roas"] is not None else "no conversion value reported")]))
        wk = m["weekly"]
        parts.append(_cards([
            ("Ad spend per week", "All campaigns",
             svg_columns([w["week"][5:] for w in wk], _z([w["spend"] for w in wk]), "spent"),
             _table(["Week", "Spend", "Conversions"], [[w["week"], money(w["spend"]), _na(w["conversions"])] for w in wk])),
            ("Spend by campaign this week", "",
             svg_rows([r["campaign"] for r in m["campaigns"]], [r["spend"] for r in m["campaigns"]], fmt=lambda v: f"{v:,.2f}"),
             _table(["Campaign", "Spend", "Clicks", "CPC", "Conversions", "ROAS"],
                    [[r["campaign"], money(r["spend"]), r["clicks"], money(r["cpc"]), r["conversions"], _na(r["roas"])] for r in m["campaigns"]])),
        ]))
    else:
        parts.append(na)

    st_rows = []
    for v in k["sources"].values():
        state = ("not connected" if not v["connected"] else f"has data, {v['report_week_days']} of 7 days" if v["has_report_week"]
                 else f"no data ({v['last_status']})")
        st_rows.append([v["label"], state, v["last_sync"] or "never"])
    status = ('<details class="status" open><summary>Data sources</summary><table><tr><th>Source</th><th>Report week</th><th>Last sync</th></tr>'
              + "".join("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in r) + "</tr>" for r in st_rows) + "</table></details>")

    data_json = json.dumps(k, default=str).replace("</", "<\\/")
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Marketing dashboard - {_e(site)}</title><style>{CSS}</style></head>
<body><div class="viz-root"><div class="wrap">
<h1>Weekly marketing dashboard</h1><div class="sub">{_e(site)} &middot; week {_e(rw['start'])} to {_e(rw['end'])} &middot; generated {_e(generated)}</div>
<div class="card" style="margin-top:16px"><h2>This week in plain English</h2><p class="summary">{_e(summary)}</p>{status}</div>
{''.join(parts)}
<div class="foot">Built by marketing-analytics-reports from Google Analytics 4, Search Console and Meta Ads API data. Raw numbers: kpis.json next to this file.</div>
</div></div><div id="tip"></div><script>{JS}</script>
<script type="application/json" id="kpi-data">{data_json}</script></body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    return path
