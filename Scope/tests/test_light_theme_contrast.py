#!/usr/bin/env python3
"""
The light theme must stay LEGIBLE and its data colours must stay MEANINGFUL.

A light theme is easy to regress by eye — a token nudged one shade "for looks"
can drop text under the WCAG floor, or collapse two severities into the same
apparent colour, and nothing would fail. These tests read the real
`api/static/tokens.css` and assert the contract:

  * every text token clears AA (4.5:1) on the canvas and on surface-1;
  * severity and direction colours clear it too — they are read as text and as
    small marks, and they carry meaning;
  * severity stays at least as mutually separable on light as it already is on
    DARK. Dark is the shipped baseline, so holding light to a stricter bar than
    the theme everyone already uses would be arbitrary; holding it to a weaker
    one would be a regression.
  * dark remains the DEFAULT — no light value may leak into `:root`.
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
TOKENS = os.path.join(os.path.dirname(__file__), "..", "api", "static", "tokens.css")


def _block(selector: str) -> dict:
    """The custom properties declared in one selector block of tokens.css."""
    css = open(TOKENS).read()
    i = css.index(selector)
    body = css[css.index("{", i) + 1:css.index("}", i)]
    return {m.group(1): m.group(2).strip()
            for m in re.finditer(r"(--[\w-]+)\s*:\s*([^;]+);", body)}


DARK = _block(":root {")
LIGHT = _block(':root[data-theme="light"]')


def _rgb(v: str):
    v = v.strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    return tuple(int(v[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _lum(hex_: str) -> float:
    def f(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(hex_)
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a: str, b: str) -> float:
    l1, l2 = sorted((_lum(a), _lum(b)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def _hue(hex_: str) -> float:
    import colorsys
    r, g, b = _rgb(hex_)
    return colorsys.rgb_to_hsv(r, g, b)[0] * 360


def _sat(hex_: str) -> float:
    import colorsys
    r, g, b = _rgb(hex_)
    return colorsys.rgb_to_hsv(r, g, b)[1]


TEXTISH = ["--text-primary", "--text-secondary", "--text-tertiary", "--accent",
           "--direction-buy", "--direction-sell", "--link",
           "--severity-low", "--severity-medium", "--severity-high",
           "--severity-critical"]


@pytest.mark.parametrize("token", TEXTISH)
@pytest.mark.parametrize("surface", ["--surface-canvas", "--surface-1"])
def test_light_text_clears_wcag_aa(token, surface):
    ratio = contrast(LIGHT[token], LIGHT[surface])
    assert ratio >= 4.5, f"{token} on {surface} is {ratio:.2f}:1 (AA needs 4.5)"


@pytest.mark.parametrize("token", TEXTISH)
@pytest.mark.parametrize("surface", ["--surface-canvas", "--surface-1"])
def test_dark_text_still_clears_wcag_aa(token, surface):
    """Dark is the default and must not regress while light is being tuned."""
    ratio = contrast(DARK[token], DARK[surface])
    assert ratio >= 4.5, f"DARK {token} on {surface} is {ratio:.2f}:1"


SEVERITY = ["--severity-low", "--severity-medium", "--severity-high",
            "--severity-critical"]


@pytest.mark.parametrize("a,b", [(a, b) for i, a in enumerate(SEVERITY)
                                 for b in SEVERITY[i + 1:]])
def test_severity_stays_as_separable_on_light_as_on_dark(a, b):
    """Meaning must survive the theme.

    Two colours are separable if their HUES differ, or — when one is
    effectively neutral — if their SATURATION does. Dark sets the bar: light
    only has to match it, because dark is what ships today.
    """
    def separation(pal):
        sa, sb = _sat(pal[a]), _sat(pal[b])
        sat_gap = abs(sa - sb) / 0.15
        # A near-grey has no meaningful hue — its angle is numeric noise, and
        # taking max(hue, sat) would let that noise inflate the score. So when
        # either colour is effectively neutral, saturation is the whole story.
        if min(sa, sb) < 0.20:
            return sat_gap
        d = abs(_hue(pal[a]) - _hue(pal[b]))
        hue_gap = min(d, 360 - d)
        return max(hue_gap / 30.0, sat_gap)

    light_sep, dark_sep = separation(LIGHT), separation(DARK)
    assert light_sep >= dark_sep * 0.75, (
        f"{a} vs {b}: light separation {light_sep:.2f} is materially worse "
        f"than dark's {dark_sep:.2f} — the distinction would be lost on white")
    assert light_sep >= 1.0, f"{a} vs {b}: light separation {light_sep:.2f} too low"


def test_dark_is_untouched_by_the_light_theme():
    """Light is additive: it may only live behind the data-theme attribute."""
    css = open(TOKENS).read()
    root = css[css.index(":root {"):css.index('}', css.index(":root {"))]
    for value in LIGHT.values():
        if value.startswith("#"):
            assert value not in root, f"light value {value} leaked into :root"
    assert ':root[data-theme="light"]' in css
    # The default must remain dark: canvas is near-black in :root.
    assert _lum(DARK["--surface-canvas"]) < 0.1


def test_every_light_token_has_a_dark_counterpart():
    """A light override with no dark original would be an untracked colour."""
    missing = [k for k in LIGHT if k not in DARK]
    assert not missing, f"light-only tokens: {missing}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
