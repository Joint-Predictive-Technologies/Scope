#!/usr/bin/env python3
"""Detect and repair text that was written as UTF-8 and read as a single-byte codec.

    >>> repair_mojibake("MÃ¼nchen")
    'München'
    >>> repair_mojibake("München") is None      # already correct
    True

── 🔴 WHAT THIS IS NOT FOR ──────────────────────────────────────────────────
It is NOT a fix for a decoding bug in this repo. There is no such bug in the
patent path: `scratchpad/geocode/resolve.py` reads a UTF-8 JSON pull correctly
and copies the source's `city`/`state`/`country` verbatim, which is exactly what
it documents itself as doing.

**PatentsView publishes the mojibake.** Confirmed against LIVE BigQuery, not
against the local pull:

    patents-public-data.patentsview.location  ->  'BÃ¶blingen'  'DÃ¼sseldorf'
                                                  'EckernfÃ¶rde'  'CarrÃ¨'

and the source carries BOTH spellings as separate rows under separate uuids:
`Zürich` and `ZÃ¼rich` both exist under country `AT`, and `ZÃ¼rich` appears again
under CH, DE, FI, CA and SZ.

⚠️ **248 IS A ROW COUNT, NOT A PAIR COUNT.** The first version of this comment
said "248 such pairs"; a verifier re-derived it. 248 mangled rows have a clean
twin string somewhere in the table — the distinct PAIRS are **230** on city
alone, **214** on (city, country).
So this module normalises a PRESENTATION field over data we do not control. It must never
be applied to a column whose job is to record what the source actually said.

── the two codecs, and why one is not enough ───────────────────────────────
    latin-1  maps 0x80-0x9F to control characters
    cp1252   maps them to printable ones (0x98 -> U+02DC '˜'), which latin-1
             cannot re-encode at all
Measured on the live graph: **cp1252 alone repairs all 61; latin-1 alone repairs
53.** A name needs to round-trip under EITHER codec, never both — so a claim of
the form "the inverse holds for codec in (latin-1, cp1252)" is false for 8 of the
61 and should be worded as "under at least one of".

── 🔴 A FLOOR, NOT A CENSUS ─────────────────────────────────────────────────
Text mangled repeatedly can stop being reversible. `repair_mojibake` returns None
for those, and `looks_mangled` exists so they can be COUNTED AND NAMED rather
than silently dropping out of the total. One is live in the graph today:

    'University Of PittsburghÃ¢â‚¬â€ Of The Commonwealth System of Higher Education'

Any consumer reporting a count must report both numbers.
"""
from __future__ import annotations

# order matters only for tie-breaking; both are tried
CODECS = ("latin-1", "cp1252")
MAX_PASSES = 4          # mangling can be applied more than once

# byte-sequence tells for UTF-8 read as a single-byte codec. Deliberately used
# ONLY to widen the net for `looks_mangled`, never to decide a repair — a
# character blacklist is wrong in both directions and 7 of the graph's 61 real
# cases contain no 'Ã' at all.
_TELLS = ("Ã", "Â", "â€", "Î²", "Å›", "È©", "Ã¢", "â\x80")


def repair_mojibake(name: str | None) -> str | None:
    """The repaired string, or None when `name` is not reversibly mis-decoded.

    Structural: re-encode to a single-byte codec and decode as UTF-8. A correctly
    encoded name either fails to re-encode or round-trips to itself.
    """
    if not name:
        return None
    best = None
    for cs in CODECS:
        cur = name
        for _ in range(MAX_PASSES):
            try:
                nxt = cur.encode(cs).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                break
            if nxt == cur:
                break
            cur = nxt
        if cur == name:
            continue
        # 🔴 THE CODECS CANNOT DISAGREE, AND THIS ASSERTS IT RATHER THAN RANKING
        # THEM.  An earlier version took `min(..., key=len)` — "shortest wins" —
        # and a verifier showed the whole suite stayed green when that was flipped
        # to longest-wins, because the branch is unreachable. Brute-forced over
        # 17,919 mangled forms: both codecs succeed and AGREE 6,016 times, exactly
        # one succeeds 11,903 times, and they produce different answers ZERO times.
        # cp1252 differs from latin-1 only on 0x80-0x9F, so a mangled string either
        # contains a character from that range — and then only one codec can
        # re-encode it — or it does not, and they are the same codec for it.
        # If that ever stops holding, this raises instead of silently picking.
        assert best is None or best == cur, (
            f"codecs disagree on {name!r}: {best!r} vs {cur!r} — the selection rule "
            f"this module says it does not need is now needed")
        best = cur
    return best


def looks_mangled(name: str | None) -> bool:
    """A WIDER net than `repair_mojibake`, for counting what cannot be repaired.

    Never use this to decide a repair — it has false positives by construction.
    Its only job is to stop an irreversible case from vanishing from a total.
    """
    return bool(name) and any(t in name for t in _TELLS)


def classify(name: str | None) -> str:
    """'clean' | 'reversible' | 'irreversible'"""
    if repair_mojibake(name):
        return "reversible"
    return "irreversible" if looks_mangled(name) else "clean"
