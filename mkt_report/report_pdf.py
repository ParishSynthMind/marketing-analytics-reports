"""PDF report: summary, source status, KPI tiles, weekly charts and tables per source.

'Page N of M' on every page; the report date top right on page 1 only.
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Dict, List, Optional

import matplotlib
import matplotlib.ticker

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_LEFT  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.pdfgen import canvas as rl_canvas  # noqa: E402
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle  # noqa: E402

BLUE = "#2a78d6"
BLUE_LIGHT = "#a9c8ef"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
GOOD, BAD = "#1d7a46", "#b3261e"


class NumberedCanvas(rl_canvas.Canvas):
    """Two-pass canvas so every page knows the total page count."""

    report_date = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self._decorate(total)
            super().showPage()
        super().save()

    def _decorate(self, total: int):
        w, h = A4
        self.setFont("Helvetica", 8.5)
        self.setFillColor(colors.HexColor(INK_2))
        self.drawCentredString(w / 2, 10 * mm, f"Page {self._pageNumber} of {total}")
        if self._pageNumber == 1:
            self.setFont("Helvetica", 10)
            self.drawRightString(w - 18 * mm, h - 14 * mm, self.report_date)


def _png(fig) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def week_chart(labels: List[str], values: List[Optional[float]], title: str, money: bool = False) -> io.BytesIO:
    """Weekly columns; the report week is solid, earlier weeks lighter. Missing weeks are gaps, not zeros."""
    fig, ax = plt.subplots(figsize=(7.2, 2.3))
    vals = [v if v is not None else 0 for v in values]
    cols = [BLUE_LIGHT] * (len(vals) - 1) + [BLUE]
    bars = ax.bar(labels, vals, color=cols, width=0.6)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=8, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.set_ylim(0, max(max(vals, default=0) * 1.18, 1))
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_title(title, loc="left", fontsize=10, color=INK, pad=10)
    for b, v in zip(bars, values):
        txt = "no data" if v is None else (f"{v:,.0f}" if not money else f"{v:,.0f}")
        ax.text(b.get_x() + b.get_width() / 2, b.get_height(), txt, ha="center", va="bottom", fontsize=7, color=INK_2)
    return _png(fig)


def _fmt(v, kind: str = "int") -> str:
    if v is None:
        return "n/a"
    if kind == "money":
        return f"{v:,.2f}"
    if kind == "pct":
        return f"{v:g}%"
    if kind == "float":
        return f"{v:g}"
    return f"{v:,.0f}"


def _chg(v, higher_is_better=True, points=False) -> str:
    if v is None:
        return "<font color='#7a7974'>no comparison</font>"
    if v == 0:
        return "no change"
    good = (v > 0) == higher_is_better
    sign = "+" if v > 0 else ""
    unit = " pts" if points else "%"
    return f"<font color='{GOOD if good else BAD}'>{sign}{v:g}{unit}</font> vs previous week"


def _tiles(body, items):
    cells = [[Paragraph(f"<font size=8 color='{INK_2}'>{t}</font><br/><font size=15>{v}</font><br/><font size=7.5>{sub}</font>", body)
              for t, v, sub in items]]
    t = Table(cells, colWidths=[43.5 * mm] * len(items), rowHeights=[19 * mm])
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(GRID)),
                           ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor(GRID)),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 6)]))
    return t


def _table(rows, col_widths=None):
    t = Table(rows, hAlign="LEFT", colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5), ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor(INK)),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor(INK_2)),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor(GRID)),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _short(s: str, n: int = 52) -> str:
    return s if len(s) <= n else s[: n - 1] + "..."


def build_pdf(k: Dict, summary: str, summary_source: str, path: str, site: str, report_date: datetime,
              notes: List[str] = ()) -> str:
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], alignment=TA_LEFT, fontSize=18, textColor=colors.HexColor(INK), spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12.5, textColor=colors.HexColor(INK), spaceBefore=12, spaceAfter=5)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=10, leading=14, textColor=colors.HexColor(INK))
    small = ParagraphStyle("s", parent=body, fontSize=8.5, leading=11, textColor=colors.HexColor(INK_2))
    rw, pw = k["report_week"], k["previous_week"]
    week_labels = [w["week"][5:] for w in (k["ga4"] or k["gsc"] or k["meta"] or {"weekly": []})["weekly"]]

    story = [Paragraph("Weekly marketing report", h1),
             Paragraph(f"{_esc(site)} &middot; week {rw['start']} to {rw['end']} &middot; "
                       f"compared with {pw['start']} to {pw['end']}", small),
             Spacer(1, 6), Paragraph("Summary", h2)]
    for line in summary.splitlines():
        if line.strip():
            story.append(Paragraph(_esc(line.strip()), body))
    story.append(Spacer(1, 3))
    story.append(Paragraph("Written by Claude from the numbers in this report. Every figure is checked against the data; "
                           "any that are not found are listed at the end."
                           if summary_source == "claude" else "Template summary (AI summary not used).", small))

    src_rows = [["Source", "Report week", "Days with data", "Last sync"]]
    for v in k["sources"].values():
        state = ("not connected" if not v["connected"] else "has data" if v["has_report_week"] else
                 f"no data ({v['last_status']})")
        src_rows.append([v["label"], state, f"{v['report_week_days']} of 7" if v["has_report_week"] else "-",
                         v["last_sync"] or "never"])
    story += [Paragraph("Data sources", h2), _table(src_rows, [55 * mm, 40 * mm, 30 * mm, 45 * mm])]

    # ---- website ----
    g = k["ga4"]
    story.append(Paragraph("Website (Google Analytics 4)", h2))
    if g:
        t, c = g["this_week"], g["change_pct"]
        story.append(_tiles(body, [
            ("Sessions", _fmt(t["sessions"]), _chg(c["sessions"])),
            ("Users", _fmt(t["users"]), _chg(c["users"])),
            ("Key events", _fmt(t["key_events"], "float"), _chg(c["key_events"])),
            ("Engagement rate", _fmt(t["engagement_rate_pct"], "pct"), f"key event rate {_fmt(t['key_event_rate_pct'], 'pct')}"),
        ]))
        story.append(Image(week_chart(week_labels, [w["sessions"] for w in g["weekly"]], "Sessions per week (week starting)"),
                           width=170 * mm, height=54 * mm))
        rows = [["Top pages this week", "Views", "Previous week", "Change"]] + [
            [_short(p["page"]), _fmt(p["views"]), _fmt(p["prev_views"]), _fmt(p["change_pct"], "pct")] for p in g["top_pages"]]
        story.append(KeepTogether([Spacer(1, 4), _table(rows, [90 * mm, 25 * mm, 30 * mm, 25 * mm])]))
    else:
        story.append(Paragraph("Not available for this week. See Data sources above; this is never shown as zero.", body))

    # ---- search ----
    s = k["gsc"]
    block = [Paragraph("Google search (Search Console)", h2)]
    if s:
        t, c = s["this_week"], s["change_pct"]
        block.append(_tiles(body, [
            ("Clicks", _fmt(t["clicks"]), _chg(c["clicks"])),
            ("Impressions", _fmt(t["impressions"]), _chg(c["impressions"])),
            ("Click-through rate", _fmt(t["ctr_pct"], "pct"), _chg(s["ctr_change_points"], points=True)),
            ("Average position", _fmt(t["avg_position"], "float"),
             _chg(s["position_change"], higher_is_better=False, points=True).replace(" pts", "")),
        ]))
        partial = f" (report week: {t['days_with_data']} of 7 days so far)" if t["days_with_data"] < 7 else ""
        block.append(Image(week_chart(week_labels, [w["clicks"] for w in s["weekly"]], "Clicks from Google search per week" + partial),
                           width=170 * mm, height=54 * mm))
        story.append(KeepTogether(block))
        rows = [["Top queries this week", "Clicks", "Impressions", "CTR", "Position", "Prev. clicks"]] + [
            [_short(q["query"], 40), _fmt(q["clicks"]), _fmt(q["impressions"]), _fmt(q["ctr_pct"], "pct"),
             _fmt(q["avg_position"], "float"), _fmt(q["prev_clicks"])] for q in s["top_queries"]]
        story.append(KeepTogether([Spacer(1, 4), _table(rows, [62 * mm, 20 * mm, 24 * mm, 18 * mm, 20 * mm, 26 * mm])]))
    else:
        story += block + [Paragraph("Not available for this week. See Data sources above; this is never shown as zero.", body)]

    # ---- paid social ----
    m = k["meta"]
    block = [Paragraph("Paid social (Meta Ads)", h2)]
    if m:
        t, c = m["this_week"], m["change_pct"]
        block.append(_tiles(body, [
            ("Spend", _fmt(t["spend"], "money"), _chg(c["spend"], higher_is_better=False)),
            ("Cost per click", _fmt(t["cpc"], "money"), _chg(c["cpc"], higher_is_better=False)),
            ("Conversions", _fmt(t["conversions"], "float"),
             f"cost per conversion {_fmt(t['cost_per_conversion'], 'money')}"),
            ("Return on ad spend", _fmt(t["roas"], "float"), _chg(c["roas"]) if t["roas"] is not None else "no conversion value reported"),
        ]))
        block.append(Image(week_chart(week_labels, [w["spend"] for w in m["weekly"]], "Meta ad spend per week", money=True),
                           width=170 * mm, height=54 * mm))
        story.append(KeepTogether(block))
        rows = [["Campaign", "Spend", "Clicks", "CPC", "Conversions", "ROAS"]] + [
            [_short(r["campaign"], 40), _fmt(r["spend"], "money"), _fmt(r["clicks"]), _fmt(r["cpc"], "money"),
             _fmt(r["conversions"], "float"), _fmt(r["roas"], "float")] for r in m["campaigns"]]
        story.append(KeepTogether([Spacer(1, 4), _table(rows, [66 * mm, 22 * mm, 18 * mm, 18 * mm, 24 * mm, 22 * mm])]))
    else:
        story += block + [Paragraph("Not available for this week. See Data sources above; this is never shown as zero.", body)]

    story.append(KeepTogether([Spacer(1, 6), Paragraph("How these are counted", h2)] + [Paragraph(_esc(n), small) for n in [
        "Weeks run Monday to Sunday; the report week is the last complete one. Users are de-duplicated across the "
        "week by GA4 (isoYearIsoWeek), not added up day by day.",
        "Search totals come from site-level rows. Google leaves rare (anonymised) queries out of the query table, so "
        "the top queries add up to less than the total. Average position is weighted by impressions; lower is better. "
        "Search Console data usually trails by two to three days, so the latest week can be incomplete.",
        "Meta conversions count only the action types you configure (META_CONVERSION_ACTIONS), so the same sale "
        "is not counted three times. ROAS is conversion value divided by spend, shown only where a value was reported.",
        *notes]]))

    NumberedCanvas.report_date = report_date.strftime("%d %B %Y")
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=20 * mm, bottomMargin=18 * mm,
                            title="Weekly marketing report", author="marketing-analytics-reports")
    doc.build(story, canvasmaker=NumberedCanvas)
    return path
