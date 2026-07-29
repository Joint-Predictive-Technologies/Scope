#!/usr/bin/env python3
"""
No page may hardcode a colour and quietly stay dark under the light theme.

`test_light_theme_contrast.py` reads tokens.css, so it is structurally blind to
this class of bug: a page can pass every token assertion and still paint a
black nav bar, a black WebGL stage, or white-on-white numerals, because the
colour never went through a token. That is exactly what the verifier found on
the first pass — five real holdouts, none of them visible to a token test.

So this test looks at the PAGES. A literal is allowed only when it is one of:

  * a keep-dark surface that DECLARES itself (`data-theme-lock`), so the
    decision is stated in the page and cannot drift into an accident;
  * an ink that must contrast with a MARK rather than with the page (a label
    on a ball, a numeral on a heatmap cell) and is chosen per theme in code;
  * a documented neutral that reads on both grounds.

Anything else is a holdout and fails here.
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.join(os.path.dirname(__file__), "..")
STATIC = os.path.join(ROOT, "api", "static")

# Colour literals sitting in a CSS colour position — the ones that paint a page.
CSS_COLOUR = re.compile(
    r"(color|background(?:-color)?|border(?:-[a-z]+)?|fill|stroke)"
    r"\s*:\s*[^;\"'}\n]*?(#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\))"
)
# WebGL/canvas colours never appear in a CSS position and are missed by a
# `#hex|rgb()` sweep entirely — 0x literals are how /osint stayed black.
HEX_0X = re.compile(r"0x[0-9a-fA-F]{6}\b")
# ...but only where the line is actually about colour: 0xC0FFEE is a PRNG seed.
COLOURISH = re.compile(r"[Cc]olor|[Ll]ight\(|[Mm]aterial|fill|tint|emissive|setClear")

# ── Per-SITE opt-out, not per-file. ─────────────────────────────────────────
# A file-wide allowlist hides the next holdout that lands in an already-listed
# file, so the escape hatch is a marker on the line itself:
#
#     ctx.fillStyle = isLight() ? '#faf9f7' : '#0a0a0c';   // theme-ok: ink on the ball
#
# The marker has to carry a reason, which makes the decision reviewable in the
# diff instead of buried in a test fixture.
MARKER = "theme-ok"
# Neutral greys that read on both grounds — deliberately left literal.
NEUTRAL = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*[,)]")


def _is_neutral(value: str) -> bool:
    m = NEUTRAL.match(value)
    if not m:
        return False
    r, g, b = (int(x) for x in m.groups())
    return max(r, g, b) - min(r, g, b) <= 8 and 60 <= max(r, g, b) <= 200


def _pages():
    for name in sorted(os.listdir(STATIC)):
        if name.endswith((".html", ".js")) and name != "tokens.css":
            yield name, os.path.join(STATIC, name)
    yield "morning_brief.py", os.path.join(ROOT, "scripts", "morning_brief.py")


@pytest.mark.parametrize("name,path", list(_pages()), ids=lambda v: v if isinstance(v, str) else "")
def test_no_page_hardcodes_a_colour_it_should_theme(name, path):
    src = open(path, encoding="utf-8").read()
    bad = []
    for i, line in enumerate(src.splitlines(), 1):
        if MARKER in line:
            assert line.split(MARKER, 1)[1].strip(" :*/-"), f"{name}:{i} {MARKER} with no reason"
            continue
        for m in CSS_COLOUR.finditer(line):
            if not _is_neutral(m.group(2)):
                bad.append(f"{name}:{i} {m.group(1)}: {m.group(2)}")
        if COLOURISH.search(line):
            for m in HEX_0X.finditer(line):
                bad.append(f"{name}:{i} WebGL literal {m.group(0)}")
    assert not bad, (
        "hardcoded colour(s) that will not follow the theme — route them through a "
        "token, or mark the line `theme-ok: <reason>` if it contrasts with a mark "
        "rather than the page:\n  "
        + "\n  ".join(bad)
    )


def test_the_keep_dark_page_actually_declares_itself():
    """A keep-dark page must PIN itself, not merely happen to look dark."""
    src = open(os.path.join(STATIC, "osint.html"), encoding="utf-8").read()
    assert 'data-theme-lock' in src, "osint.html keeps dark but does not declare it"
    lock = src.index("data-theme-lock")
    theme_js = src.index("/theme.js")
    assert lock < theme_js, "the lock must be set BEFORE theme.js reads it"


def test_theme_js_honours_the_lock():
    src = open(os.path.join(STATIC, "theme.js"), encoding="utf-8").read()
    assert "data-theme-lock" in src, "theme.js ignores the lock a page declares"


def test_every_page_loads_the_theme_script_synchronously():
    """Deferring it produces a dark flash on every load for a light user."""
    missing, deferred = [], []
    for name, path in _pages():
        if not name.endswith(".html"):
            continue
        src = open(path, encoding="utf-8").read()
        m = re.search(r"<script[^>]*src=[\"']/theme\.js[\"'][^>]*>", src)
        if not m:
            missing.append(name)
        elif "defer" in m.group(0) or "async" in m.group(0):
            deferred.append(name)
    assert not missing, f"pages with no theme script: {missing}"
    assert not deferred, f"theme script deferred (causes a flash of dark): {deferred}"


def test_the_brief_template_themes_its_nav():
    """The brief serves at `/` — a black nav bar there is the whole site's first
    impression, and it was the worst light failure of the first pass (1.95:1)."""
    src = open(os.path.join(ROOT, "scripts", "morning_brief.py"), encoding="utf-8").read()
    nav = re.search(r"\nnav\{[^}]*\}", src)
    assert nav, "brief template has no nav rule"
    assert "var(--surface-nav)" in nav.group(0), f"brief nav is not tokenised: {nav.group(0)[:160]}"
    assert "/theme.js" in src, "brief template does not load the theme script"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
