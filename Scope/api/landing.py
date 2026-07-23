"""
Landing resolution — what `/` shows (Phase 2: brief as default landing).

Policy: show today's cached morning brief; if it isn't generated yet (before the
06:30 UTC DAILY_BRIEF job, or if that job failed), fall back to yesterday's brief
with a notice; if no brief exists at all (fresh DB), fall back to the live feed
with a notice.

**Never generates a brief on a page load.** Briefs are produced only by the
scheduled DAILY_BRIEF job; this module only READS the `briefs` cache table. That
keeps the entry point fast and out-of-band from generation.
"""
from __future__ import annotations

import html as _html
from typing import Optional, Tuple


def _cached_brief_html(conn, date: str) -> Optional[str]:
    """Cached brief HTML for a date, or None. Read-only; never generates."""
    try:
        row = conn.execute(
            "SELECT html FROM briefs WHERE date = ? AND html IS NOT NULL AND html != ''",
            (date,),
        ).fetchone()
    except Exception:
        # briefs table may not exist yet on a fresh DB — treat as "no brief".
        return None
    if not row:
        return None
    try:
        return row["html"]
    except (TypeError, IndexError, KeyError):
        return row[0]


def resolve_landing(conn, today: str, yesterday: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Decide what to serve at `/`.

    Returns (mode, html, notice):
      - ("brief", <html>, None)         today's brief exists
      - ("brief", <html>, <notice>)     today missing → yesterday + notice
      - ("feed",  None,   None)         no brief at all → caller serves the feed
    """
    html = _cached_brief_html(conn, today)
    if html:
        return ("brief", html, None)
    html = _cached_brief_html(conn, yesterday)
    if html:
        return ("brief", html,
                f"Today's brief runs at 06:30 UTC — showing yesterday's ({yesterday}).")
    return ("feed", None, None)


# Fallback-notice banner ONLY. The old bar also carried a "Morning Brief |
# See raw feed →" toggle; that toggle is removed — the main page IS the
# experience and the feed is now a first-class nav tab, so the toggle was
# redundant chrome. This banner appears only when we're serving a fallback
# (yesterday's brief), to explain why. Static (not sticky) so it never overlaps
# the sticky nav. Tokenized (the brief links /tokens.css).
_BAR_TMPL = (
    '<div class="scope-brief-bar" style="'
    'background:var(--surface-2,#181610);border-bottom:1px solid var(--border-subtle,#2a2620);'
    'color:var(--text-secondary,#c8bfa8);'
    'font-family:var(--font-mono,monospace);font-size:.72rem;padding:8px 16px;text-align:center'
    '">{left}</div>'
)


def inject_brief_header(html: str, notice: Optional[str] = None) -> str:
    """Prepend a fallback-notice banner to the cached brief HTML, but ONLY when
    there is a notice (i.e. we're serving yesterday's brief). In the normal case
    (today's brief) nothing is injected — the page stands on its own."""
    if not notice:
        return html
    bar = _BAR_TMPL.format(left=_html.escape(notice))
    if "<body>" in html:
        return html.replace("<body>", "<body>" + bar, 1)
    return bar + html
