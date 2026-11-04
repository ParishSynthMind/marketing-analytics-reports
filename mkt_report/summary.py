"""Weekly summary in plain English. Claude writes it from the KPI numbers only; a template is the fallback."""
from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional, Tuple

DEFAULT_MODEL = "claude-sonnet-5-5"

SYSTEM = (
    "You write the weekly summary at the top of a small business's marketing report covering website traffic "
    "(Google Analytics 4), Google search (Search Console) and paid social (Meta Ads). "
    "Use ONLY the numbers in the KPI JSON you are given; never invent, estimate, extrapolate or calculate a new figure. "
    "Percentage changes are already in the JSON as change_pct; use those rather than working your own out. "
    "If a source is null or not connected, say it is not available rather than guessing. "
    "If a source has fewer than 7 report_week_days, mention that its week is incomplete. "
    "For Search Console average position, a lower number is better. "
    "Call GA4 key events 'key events'; you do not know what they track, so do not rename them (not sales, not sign-ups). "
    "Write for a busy owner: plain English, no jargon, no hype, no emojis, no markdown. "
    "Format: one opening sentence on how the week went, then exactly three short lines starting with "
    "'Good:', 'Watch:' and 'Next step:'. Keep it under 140 words. Money has no currency symbol; "
    "write it with two decimals or a thousands separator."
)


def _chg(v) -> str:
    return "n/a" if v is None else f"{'+' if v > 0 else ''}{v:g}%"


def template_summary(k: Dict) -> str:
    g, s, m = k.get("ga4"), k.get("gsc"), k.get("meta")
    parts = []
    if g:
        t = g["this_week"]
        parts.append(f"{t['sessions']:,} website sessions ({_chg(g['change_pct']['sessions'])} on the week before) "
                     f"and {t['key_events']:g} key events")
    if s:
        t = s["this_week"]
        parts.append(f"{t['clicks']:,} clicks from Google search ({_chg(s['change_pct']['clicks'])})")
    if m:
        t = m["this_week"]
        parts.append(f"{t['spend']:,.2f} spent on Meta ads for {t['conversions']:g} conversions")
    opening = ("Week of " + k["report_week"]["start"] + ": " + "; ".join(parts) + ".") if parts else \
        "No source returned data for the report week, so there is nothing to summarise."
    good = "Good: " + (f"the top page was {g['top_pages'][0]['page']}." if g and g["top_pages"] else
                       "data is flowing from every connected source." if parts else "nothing to report.")
    lines = [opening, good]
    missing = [v["label"] for v in k["sources"].values() if not v["has_report_week"]]
    partial = [v["label"] for v in k["sources"].values() if v["has_report_week"] and v["report_week_days"] < 7]
    watch = []
    if missing:
        watch.append("no data this week from " + ", ".join(missing))
    if partial:
        watch.append("incomplete week from " + ", ".join(partial))
    if m and m["this_week"]["roas"] is not None:
        watch.append(f"Meta return on ad spend is {m['this_week']['roas']:g}")
    lines.append("Watch: " + ("; ".join(watch) if watch else "nothing unusual in the numbers.") + ".")
    top_q = s["top_queries"][0]["query"] if s and s["top_queries"] else None
    lines.append("Next step: " + (f"check the page ranking for \"{top_q}\", the top search query." if top_q else
                                  "connect the missing sources so the report has the full picture."))
    return "\n".join(lines).replace("..", ".")


def _norm(n: str) -> str:
    n = n.replace(",", "").rstrip(".")
    return str(float(n)).rstrip("0").rstrip(".") if n else n


def unverified_figures(text: str, k: Dict) -> List[str]:
    """Numbers in the summary that don't appear anywhere in the KPI data (exact or rounded).

    Dates and anything Claude worked out itself land here, so this is a review flag
    printed with the report, not a hard block.
    """
    data_nums = set()
    for n in re.findall(r"-?\d+\.?\d*", json.dumps(k, default=str)):
        n = n.lstrip("-")
        f = float(n)
        data_nums.update({_norm(n), _norm(str(round(f))), _norm(f"{f:.1f}"), _norm(f"{f:.2f}")})
    found = [m for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text) if _norm(m) not in data_nums]
    return sorted(set(found))


def claude_summary(k: Dict, api_key: str, model: Optional[str] = None, client=None) -> str:
    if client is None:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL,
        # Sonnet 5.5 thinks by default and thinking counts toward max_tokens, so leave room
        # and keep effort low: this is a short, numbers-only writing job.
        max_tokens=4000,
        output_config={"effort": "low"},
        system=SYSTEM,
        messages=[{"role": "user", "content": "KPI JSON:\n" + json.dumps(k, default=str)}],
    )
    if msg.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Claude stopped with {msg.stop_reason}")
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()


def write_summary(k: Dict, use_ai: bool = True, client=None) -> Tuple[str, str]:
    """Return (summary_text, source) where source is 'claude' or 'template'."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if use_ai and (key or client):
        try:
            text = claude_summary(k, key or "", client=client)
            if text:
                return text, "claude"
        except Exception as exc:  # the report must still go out if the AI call fails
            return template_summary(k) + f"\n(AI summary unavailable: {type(exc).__name__})", "template"
    return template_summary(k), "template"
