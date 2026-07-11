#!/usr/bin/env python3
"""
Phase 3 tests: congressional cluster detection, RULE_10 -> theme auto-population,
themes API, and useful/not-useful annotations.

Runs under pytest or standalone:  python3 tests/test_phase3.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from starlette.testclient import TestClient  # noqa: E402
from api.main import app  # noqa: E402
from jpt_common import db_connection  # noqa: E402

_c = TestClient(app)


def _cleanup(conn, ticker):
    tid = conn.execute("SELECT id FROM themes WHERE primary_ticker=?", (ticker,)).fetchall()
    for (t,) in [(r[0],) for r in tid]:
        conn.execute("DELETE FROM theme_signals WHERE theme_id=?", (t,))
        conn.execute("DELETE FROM themes WHERE id=?", (t,))
    conn.execute("DELETE FROM alerts WHERE ticker=?", (ticker,))
    conn.execute("DELETE FROM transactions WHERE raw_ticker_string=?", (ticker,))
    conn.commit()


def test_cluster_detection_emits_alert():
    from scripts.rule_cluster import run
    conn = db_connection()
    _cleanup(conn, "ZCLU")
    for i, mid in enumerate(("TESTM1", "TESTM2", "TESTM3")):
        conn.execute(
            "INSERT INTO transactions (member_id, raw_ticker_string, transaction_type, transaction_date) "
            "VALUES (?, 'ZCLU', 'Purchase', date('now'))", (mid,))
    conn.commit(); conn.close()

    run(emit=True)

    conn = db_connection()
    a = conn.execute("SELECT severity, headline, opportunity_score, evidence_confidence, tags "
                     "FROM alerts WHERE rule='RULE_CLUSTER' AND ticker='ZCLU'").fetchone()
    ok = a is not None
    if ok:
        assert "buying" in a["headline"]
        assert a["opportunity_score"] and a["opportunity_score"] > 0  # scored via insert_alert
    _cleanup(conn, "ZCLU"); conn.close()
    assert ok


def test_rule10_creates_theme_and_links_signals():
    from scripts.rule_10_corroboration import run
    conn = db_connection()
    _cleanup(conn, "ZTHM")
    for rule in ("RULE_01B", "RULE_06", "RULE_08", "RULE_11"):
        conn.execute(
            "INSERT INTO alerts (rule, ticker, severity, headline, created_at) "
            "VALUES (?, 'ZTHM', 'HIGH', ?, datetime('now'))", (rule, f"{rule} ZTHM"))
    conn.commit(); conn.close()

    run(dry_run=False, window_hours=24)

    conn = db_connection()
    theme = conn.execute("SELECT id, status, signal_count, supporting_rules, opportunity_score "
                         "FROM themes WHERE primary_ticker='ZTHM'").fetchone()
    ok = theme is not None
    if ok:
        n = conn.execute("SELECT COUNT(*) FROM theme_signals WHERE theme_id=?", (theme["id"],)).fetchone()[0]
        r10 = conn.execute("SELECT theme_id FROM alerts WHERE rule='RULE_10' AND ticker='ZTHM'").fetchone()
        assert n >= 5                     # RULE_10 + 4 contributing signals
        assert r10 and r10["theme_id"] == theme["id"]
    _cleanup(conn, "ZTHM"); conn.close()
    assert ok


def test_themes_api_shapes():
    assert isinstance(_c.get("/api/themes").json(), list)
    assert _c.get("/api/themes/99999999").status_code == 404


def test_rate_annotation_toggle():
    # rate useful, then rate useful again -> cleared (toggle off)
    r1 = _c.post("/social/1/rate", json={"session_id": "pt3", "vote": "useful"}).json()
    assert r1["my_vote"] == "useful" and r1["useful"] >= 1
    r2 = _c.post("/social/1/rate", json={"session_id": "pt3", "vote": "useful"}).json()
    assert r2["my_vote"] is None
    assert _c.post("/social/1/rate", json={"session_id": "pt3", "vote": "x"}).status_code == 400
    conn = db_connection(); conn.execute("DELETE FROM alert_votes WHERE session_id='pt3'"); conn.commit(); conn.close()


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} tests passed")


if __name__ == "__main__":
    _run_all()
