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


_BAR_TMPL = (
    '<div class="scope-brief-bar" style="position:sticky;top:0;z-index:9999;'
    'background:#181610;border-bottom:1px solid #2a2620;color:#c8bfa8;'
    "font-family:'IBM Plex Mono',monospace;font-size:.72rem;padding:8px 16px;"
    'display:flex;gap:16px;align-items:center;justify-content:space-between'
    '"><span>{left}</span>'
    '<a href="/feed" style="color:#c8922a;text-decoration:none;white-space:nowrap">'
    'See raw feed &rarr;</a></div>'
)


def inject_brief_header(html: str, notice: Optional[str] = None) -> str:
    """Prepend a sticky bar to the cached brief HTML: the fallback notice (if any)
    plus an always-visible 'See raw feed →' link."""
    left = _html.escape(notice) if notice else "Morning Brief"
    bar = _BAR_TMPL.format(left=left)
    if "<body>" in html:
        return html.replace("<body>", "<body>" + bar, 1)
    return bar + html
