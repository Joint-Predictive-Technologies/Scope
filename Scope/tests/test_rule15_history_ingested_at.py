"""RULE_15's denominator may only come from post-repair rows.

The 2026-07-27 repair gated *emission* on a count of post-`REPAIR_EPOCH` rows, but
the `history` query that SELECTS the comparison denominator never filtered
`ingested_at`. So a ticker could pass the gate on two genuine rows and still be
compared against a quarantined pre-repair one. That is not hypothetical: prod alert
32542 ("BA +908% HIGH", 2026-07-28 14:35:05) landed 33 hours after the repair
shipped, comparing a real Boeing Item 2.02 8-K against a **BA Credit Card Trust**
filing (`0000929638-26-001846`, CIK 936988, SIC 6189 asset-backed securities,
items 8.01/9.01 — not an earnings release).

Every row seeded below is prod's real stored row — accession, score and
`ingested_at` as captured 2026-07-30 (see
`vault/Scope/02_Sessions/SESSION-2026-07-30-rtx-remediation.md`).

The control (`test_..._reproduces_the_fabrication`) neutralises the epoch instead of
editing the query, so the pre-fix denominator selection runs through the *real* code
path and reproduces prod's `tags.trend_pct = 908.3` exactly. Without that control
the other tests could pass for the wrong reason — a query that returns nothing at
all also emits nothing.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from jpt_common import db_connection  # noqa: E402
import scripts.rule_15_earnings_nlp as r15  # noqa: E402


# Prod's real BA rows (2026-07-30 capture). Two post-epoch Boeing Item 2.02 8-Ks,
# four pre-epoch rows — three of which are not Boeing at all.
BA_ROWS = [
    ("BA", "2026-07-28", "0001628280-26-049929", 0.7673, "2026-07-28 14:35:05"),
    ("BA", "2026-05-14", "0000929638-26-001846", 0.0761, "2026-07-11 12:49:50"),
    ("BA", "2026-05-11", "0000929638-26-001791", 0.2580, "2026-07-11 12:49:50"),
    ("BA", "2026-05-01", "0001628280-26-029094", 0.0400, "2026-07-11 12:49:49"),
    ("BA", "2026-04-22", "0001628280-26-026391", 0.8328, "2026-07-28 01:39:40"),
    ("BA", "2026-03-30", "0000929638-26-001253", 0.0360, "2026-07-11 12:49:49"),
]

# Prod's real RTX rows — every one pre-epoch. `0001193125-26-213239` is the Artiva
# Biotherapeutics 8-K, stored under ticker RTX; the misattribution is in the DB.
RTX_ROWS = [
    ("RTX", "2026-07-23", "0000101829-26-000021", 0.1160, "2026-07-23 22:26:15"),
    ("RTX", "2026-05-08", "0001193125-26-213239", 0.0040, "2026-07-11 03:48:59"),
    ("RTX", "2026-04-30", "0001140361-26-018932", 0.0780, "2026-07-11 03:49:00"),
    ("RTX", "2026-04-21", "0000101829-26-000009", 0.1790, "2026-07-11 03:48:59"),
]

# A legitimate case: two genuine post-repair Item 2.02 8-Ks, 97 days apart, with a
# real surge. This is the signal the predicate must NOT starve.
GENUINE_ROWS = [
    ("LMT", "2026-07-28", "0001628280-26-049001", 0.9000, "2026-07-28 15:00:00"),
    ("LMT", "2026-04-22", "0001628280-26-026001", 0.3000, "2026-07-28 01:00:00"),
]


def _seed(rows, ticker, cik):
    """Seed `tickers` + `earnings_sentiment`, bypassing the ingest path entirely.

    `ingested_at` has a DEFAULT, so it must be written explicitly — the whole point
    of these tests is which side of `REPAIR_EPOCH` each row falls on.
    """
    conn = db_connection()
    r15._ensure_tables(conn)
    conn.execute("INSERT OR REPLACE INTO tickers (symbol, cik) VALUES (?, ?)",
                 (ticker, cik))
    for t, fdate, acc, score, ingested in rows:
        conn.execute(
            "INSERT INTO earnings_sentiment "
            "(ticker, filing_date, accession, political_score, keyword_counts, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (t, fdate, acc, score, '{"regulatory": 3, "tariff": 2}', ingested))
    conn.commit()
    return conn


def _run_isolated(monkeypatch, ticker):
    """Drive the REAL run() with zero network.

    `_fetch_8k_filings` -> [] means nothing is ingested, so run() falls straight
    through to the emission gate and the history query against the seeded rows —
    which is exactly the code under test.
    """
    monkeypatch.setattr(r15, "_fetch_8k_filings", lambda *a, **k: [])
    monkeypatch.setattr(r15, "TRACKED_TICKERS", [ticker])
    monkeypatch.setattr(r15.time, "sleep", lambda *a, **k: None)
    r15.run(emit=True)


def _alerts(ticker):
    conn = db_connection()
    return conn.execute(
        "SELECT headline, tags FROM alerts WHERE rule = 'RULE_15' AND ticker = ?",
        (ticker,)).fetchall()


def test_the_history_query_filters_on_the_epoch():
    """The predicate exists on the denominator source, on the gate's own basis."""
    src = open(r15.__file__).read()
    hist = src.split("# ── Signal: QoQ keyword density surge")[1].split("fetchall()")[0]
    assert "ingested_at >= ?" in hist, "history query must filter ingested_at"
    assert "REPAIR_EPOCH" in hist, "must read the same epoch as the emission gate"


def test_BA_no_longer_emits_the_fabricated_908_percent(monkeypatch):
    """BA passes the gate on 2 real Boeing rows, and now compares against one.

    Prior becomes the real 2026-04-22 Boeing 8-K (0.8328), not the BA Credit Card
    Trust row (0.0761): -7.9%, below the +50 threshold, so nothing is emitted.
    """
    _seed(BA_ROWS, "BA", "0000012927")
    _run_isolated(monkeypatch, "BA")

    assert _alerts("BA") == [], "BA must not emit — the real prior is -7.9%"

    # The gate itself still passes: this is NOT "no alert because nothing is usable".
    conn = db_connection()
    usable = conn.execute(
        "SELECT COUNT(*) c FROM earnings_sentiment WHERE ticker='BA' "
        "AND political_score>0 AND ingested_at >= ?", (r15.REPAIR_EPOCH,)).fetchone()["c"]
    assert usable == 2, "emission gate must still pass on the two genuine Boeing rows"

    trend = (0.7673 - 0.8328) / 0.8328 * 100
    assert round(trend, 1) == -7.9, f"expected -7.9%, got {trend:.1f}%"


def test_without_the_predicate_the_fabrication_reproduces_exactly(monkeypatch):
    """CONTROL — proves the tests above discriminate.

    Neutralising the epoch (rather than editing the query) restores pre-fix
    denominator selection through the real code path: MIN_GAP_DAYS=45 back from
    07-28 selects 2026-05-14, the BA Credit Card Trust row, and the trend is
    +908.3% — prod alert 32542's `tags.trend_pct` to the decimal.
    """
    _seed(BA_ROWS, "BA", "0000012927")
    monkeypatch.setattr(r15, "REPAIR_EPOCH", "1970-01-01 00:00:00")
    _run_isolated(monkeypatch, "BA")

    rows = _alerts("BA")
    assert len(rows) == 1, "control must reproduce the fabricated alert"
    import json
    assert json.loads(rows[0]["tags"])["trend_pct"] == 908.3, "must match prod 32542"


def test_RTX_cannot_emit_all_four_rows_are_pre_epoch(monkeypatch):
    """RTX has 0 post-repair rows, so usable < 2 and the gate stops it.

    Note honestly: this is the EXISTING emission gate, not the new predicate. The
    predicate is belt-and-braces for RTX — it additionally makes those four rows
    unselectable as anyone's denominator.
    """
    _seed(RTX_ROWS, "RTX", "0000101829")
    _run_isolated(monkeypatch, "RTX")

    assert _alerts("RTX") == [], "RTX must not emit"

    conn = db_connection()
    usable = conn.execute(
        "SELECT COUNT(*) c FROM earnings_sentiment WHERE ticker='RTX' "
        "AND political_score>0 AND ingested_at >= ?", (r15.REPAIR_EPOCH,)).fetchone()["c"]
    assert usable == 0, "all four RTX rows are pre-epoch"

    hist = conn.execute(
        "SELECT COUNT(*) c FROM earnings_sentiment WHERE ticker='RTX' "
        "AND political_score>0 AND ingested_at >= ?", (r15.REPAIR_EPOCH,)).fetchone()["c"]
    assert hist == 0, "and none may serve as a denominator"


def test_a_genuine_post_repair_comparison_still_emits(monkeypatch):
    """NO-REGRESSION — the predicate removes fabrications, not legitimate signal."""
    _seed(GENUINE_ROWS, "LMT", "0000936468")
    _run_isolated(monkeypatch, "LMT")

    rows = _alerts("LMT")
    assert len(rows) == 1, "a real two-8-K post-repair surge must still emit"

    import json
    tags = json.loads(rows[0]["tags"])
    assert tags["trend_pct"] == 200.0, f"0.30 -> 0.90 is +200%, got {tags['trend_pct']}"
    assert tags["prior_score"] == 0.3, "denominator must be the real prior quarter"
