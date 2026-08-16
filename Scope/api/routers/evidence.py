"""
Evidence endpoints — power the right-side evidence drawer.

No auth (read-only, same data the public feed already exposes). Returns a single
alert with its confidence breakdown, source, related signals on the same ticker,
and a timeline, so the drawer can render without extra round-trips.
"""
from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from jpt_common import (db_connection, rule10_rules_from_tags,
                        rule10_eligible_rules, rule10_instruments)

router = APIRouter()

# 🔴 LABELS ONLY — these used to carry a front-door URL per rule, surfaced as
# "Verify at <label> ↗". A source system's homepage is not the document, and
# several were measured dead or wrong: house.gov's ptr-pdfs directory 403s,
# lda.senate.gov 301s to a domain that then blocks, and the EDGAR entry carried
# no CIK at all. The real per-alert document is resolved server-side in
# api/routers/alerts.py::_document_urls; naming the system is context, not a link.
_SOURCE = {
    "RULE_01B": "House Clerk",   "RULE_02": "House Clerk",
    "RULE_06":  "SEC EDGAR",     "RULE_07": "Polymarket",
    "RULE_08":  "Federal Register",
    "RULE_09":  "Senate LDA",    "RULE_12": "Senate LDA (foreign)",
    "RULE_11":  "USASpending.gov",
    "RULE_13":  "FEC.gov",       "RULE_14": "PatentsView",
    "RULE_15":  "SEC EDGAR",     "RULE_OSINT": "GDELT / Google News",
}

_WHY = {
    "RULE_01B": "A member opened a brand-new disclosed position in this security.",
    "RULE_02":  "Multiple members traded this ticker within a short window (cluster).",
    "RULE_06":  "An executive's Form 4 trade was well above their historical average.",
    "RULE_07":  "A politically-linked prediction market moved sharply on volume.",
    "RULE_08":  "A proposed federal regulation references this sector.",
    "RULE_09":  "Quarterly lobbying spend spiked year-over-year.",
    "RULE_10":  "Three or more distinct INSTRUMENTS converged on this ticker within 14 days.",
    "RULE_11":  "A federal contract was awarded to this company (or its public parent).",
    "RULE_12":  "A foreign entity is disclosed lobbying in this sector.",
    "RULE_13":  "A large industry PAC contribution was recorded.",
    "RULE_14":  "A cluster of patent filings signals concentrated R&D.",
    "RULE_15":  "Political keyword density spiked in an earnings-related filing.",
    "RULE_OSINT": "A geopolitical event maps to this ticker's sector exposure.",
}


def _first_ticker(t: str) -> str:
    return (t or "").replace("$", "").split(" ")[0]


def _stored_confidence(alert: dict) -> dict:
    """THE alert's confidence: the number the engine actually stored. Never recomputed.

    ⚠️ THIS REPLACES A RECOMPUTED HEURISTIC THAT WAS LIVE ON THE HOMEPAGE AND WRONG BY UP
    TO 34 POINTS. The drawer used to render its own parallel score — instruments x10 capped
    at 60, plus severity, freshness, an insider bonus and a contract bonus — under the label
    "Confidence" with a /100 bar. On alert 32990 that produced **65** where the stored
    `evidence_confidence` is **46.0**, and because the freshness term decays with age it
    showed **80** on the day the alert fired: the divergence was WIDEST exactly when a
    reader was most likely to be looking.

    Three separate problems, any one of which is disqualifying:

      1. It was a different number wearing the real number's name. `evidence_confidence` is
         immutable, forward-only, and the column `alert_outcomes` calibration is measured
         against. A plausible reconstruction presented as that value is the confident-wrong-
         number failure this codebase keeps paying for.
      2. It MERGED THE TWO AXES THE ENGINE REFUSES TO MERGE. `evidence_confidence` answers
         "how well supported"; `opportunity_score` answers "how much opportunity remains".
         Freshness is an opportunity-side term, and folding it into a confidence figure
         re-created the single blended score the scoring model exists to avoid.
      3. It was uncalibrated and had no consumer. Nothing scored, ranked, briefed or
         measured against it — it existed only to be displayed.

    So the score is now READ, not derived: `alerts.evidence_confidence`, already present on
    the row this endpoint loads with `SELECT *`. No new query, no new computation.

    `unscored` is a real, distinct state and is reported as one rather than as a zero:
    write-path (b) rules insert raw and the 10-minute `enrich_scores` job fills the score in
    afterwards, so a very fresh alert genuinely has no score yet. Two rows were in exactly
    that state in prod while this was being written.
    """
    score = alert.get("evidence_confidence")
    if score is None:
        return {"score": None, "status": "unavailable",
                "reason": "No evidence_confidence stored for this alert."}
    if not score:
        return {"score": None, "status": "unscored",
                "reason": "Not yet scored — alerts written by the raw-insert path are "
                          "scored by the enrichment job within ~10 minutes."}
    # ⚠️ THE BASIS IS BRANCH-SPECIFIC, AND SAYING OTHERWISE WAS A FRESH INACCURACY.
    # A single flat string — "distinct corroborating instruments, weighted by source
    # quality" — is true of a RULE_10 convergence and FALSE of the ~99.99% of alerts that
    # are single-rule. `_distinct_rule_count` returns a hard 1 for every non-RULE_10 rule,
    # so their tier contribution is 0.0 and the score is the source-quality term ALONE.
    # On alert 38842 the old wording credited corroboration for a 20.0 that corroboration
    # contributed nothing to — the same class of confident-wrong sentence this whole change
    # exists to remove, reintroduced in the fix for it.
    if alert.get("rule") == "RULE_10":
        basis = ("Distinct corroborating instruments, weighted by source quality. "
                 "Frozen at detection time and never recomputed.")
    else:
        basis = (f"Single-instrument alert: this score is its source quality "
                 f"({alert.get('source_quality') or 'unknown'}) alone — corroboration "
                 f"contributes nothing to it. Frozen at detection time and never "
                 f"recomputed.")
    return {"score": score, "status": "known", "reason": None, "basis": basis}


def _evidence_provenance(alert: dict, related: list) -> dict:
    """WHAT IS BEHIND THE SCORE, as facts rather than as points.

    ⚠️ FACTS, NOT ADDENDS, AND THAT DISTINCTION IS THE WHOLE POINT. The previous version
    returned these as weighted point values ("+30", "+15") that summed to a displayed total.
    Once the displayed score is the stored one, any "+N" beside it would imply the parts add
    up to it — and they do not, and cannot: `evidence_confidence` is a tier plus a source-
    quality term, attributable to no individual rule family. So these are reported as what
    they are — a count, a severity, a leg present or absent — and nothing here is summed.

    ⚠️ THE INSTRUMENT COUNTING AND THE CORROBORATION CHECK ARE PRESERVED EXACTLY. They are
    not decoration: this is the sixth place the gate's counting gets re-expressed, and both
    branches were previously wrong in ways a verification pass caught — the RULE_10 branch
    counted rule NAMES (five names off three instruments), and the single-rule branch counted
    a leg that was present but did NOT corroborate, handing credit to a rejected
    exercise-and-sell. `rule10_instruments` and the gate's own `alert_corroborates` remain
    the only authorities; neither is re-derived here.
    """
    import datetime as _dt

    rule = alert.get("rule", "")
    try:
        ts = _dt.datetime.fromisoformat((alert.get("created_at") or "").replace(" ", "T"))
        age_h = (_dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
                 - ts).total_seconds() / 3600
    except Exception:
        age_h = None

    facts = {
        "severity": alert.get("severity"),
        "age_hours": round(age_h, 1) if age_h is not None else None,
        "source_quality": alert.get("source_quality"),
    }

    if rule == "RULE_10":
        rules = rule10_eligible_rules(rule10_rules_from_tags(alert.get("tags") or ""))
        instruments = sorted(rule10_instruments(rules))
        facts.update({
            "instruments": instruments,
            "instrument_count": len(instruments),
            "insider_leg": "RULE_06" in rules,
            "contract_leg": "RULE_11" in rules,
            "conflicting_signals": sum(
                1 for r in related if "sale" in (r.get("headline") or "").lower()),
            "eligible_rules": rules,
        })
        return facts

    # ⚠️ THIS COUNT IS CONTEXT, NOT AN INPUT TO THE SCORE, AND IT MUST SAY SO.
    # On the RULE_10 branch `instrument_count` IS the tier that drives
    # `evidence_confidence`. Here it is something else entirely: how many OTHER alerts on
    # the same ticker independently corroborate. `_distinct_rule_count` hands a hard 1 to
    # every non-RULE_10 rule, so this number contributes **nothing** to the score printed
    # beside it. Publishing both under the same key made one label mean two opposite things
    # on adjacent screens — so this branch does not emit `instrument_count` at all, and its
    # count is named for what it is.
    #
    # ⚠️ AND THE LEG MUST CORROBORATE, NOT MERELY BE PRESENT — pinned by
    # `test_a_related_leg_that_does_not_corroborate_earns_no_credit`, which did not exist
    # until a verification pass found that deleting this filter left the whole suite green.
    # The old drawer credited a rejected exercise-and-sell 8 points.
    #
    # ⚠️ CAVEAT, PRE-EXISTING AND NOT INTRODUCED HERE: `related` is matched with
    # `ticker LIKE '%tk%'` (see `alert_evidence`), so a PFE row can be returned for a P
    # alert. That was survivable while this was a buried "+8"; it is more visible now that
    # it renders as a labelled fact, and it is flagged for its own pass rather than being
    # silently repaired inside a display change.
    from scripts.rule_10_corroboration import alert_corroborates as _corroborates
    corroborating = sorted(rule10_instruments(rule10_eligible_rules(
        {r.get("rule") for r in related
         if r.get("rule") and _corroborates(r)[0]})))
    facts.update({
        "corroborating_instruments": len(corroborating),
        "corroborating_instrument_names": corroborating,
        "instruments": corroborating,
        "contributes_to_score": False,
        "conflicting_signals": 0,
        "eligible_rules": [rule] if rule else [],
    })
    return facts


@router.get("/alert/{alert_id}")
def alert_evidence(alert_id: int):
    conn = db_connection()
    a = conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    if not a:
        conn.close()
        return JSONResponse(status_code=404, content={"error": "Alert not found"})
    alert = dict(a)
    tk = _first_ticker(alert.get("ticker") or "")

    related = []
    if tk:
        related = [
            dict(r) for r in conn.execute(
                """
                SELECT id, rule, ticker, severity, headline, created_at,
                       corroborates, corroboration_note
                FROM alerts
                WHERE ticker LIKE ? AND id != ?
                  AND datetime(created_at) >= datetime('now', '-30 days')
                ORDER BY datetime(created_at) DESC LIMIT 12
                """,
                (f"%{tk}%", alert_id),
            ).fetchall()
        ]
    conn.close()

    src = _SOURCE.get(alert.get("rule", ""), "Source")

    # Contract mapping transparency (RULE_11): tags are
    # recipient|award_date|award_id|public_parent|mapping_confidence
    contract = None
    if alert.get("rule") == "RULE_11":
        parts = (alert.get("tags") or "").split("|")
        recipient = parts[0].strip() if parts else ""
        award_id = parts[2].strip() if len(parts) > 2 else ""
        parent = parts[3].strip() if len(parts) > 3 else ""
        conf = parts[4].strip() if len(parts) > 4 else ""
        # 🔴 NO SEARCH FALLBACK. A usaspending search for the recipient name is not
        # the award; it is a guess wearing the award's clothes. Prefer the typed
        # award_key column over positional tag parsing (tags[2] shifts whenever a
        # recipient name contains a pipe).
        key = (alert.get("award_key") or award_id or "").strip()
        usa = (f"https://www.usaspending.gov/award/{key}/"
               if key.upper().startswith(("CONT_AWD", "ASST_NON", "CONT_IDV")) else None)
        contract = {
            "recipient": recipient,
            "public_parent": parent or None,
            "mapping_confidence": int(conf) if conf.isdigit() else None,
            "verified_ticker": tk or None,
            "has_verified_ticker": bool(tk),
            "award_id": award_id or None,
            "awarding_agency": None,  # agency lives in headline; parsed client-side
            "usaspending_url": usa,
            "mapping_source": "Scope curated contractor table + strict token match",
        }

    return {
        "alert": alert,
        "ticker": tk,
        "why": alert.get("why_matters") or _WHY.get(alert.get("rule", ""), ""),
        # url deliberately absent: no front-door fallback, and alerts.source_url
        # is a Google News SEARCH QUERY on all 387 RULE_OSINT rows.
        "source": {"label": src},
        # `confidence` is now the STORED score and nothing else; `provenance` is the
        # supporting facts, deliberately unsummed. The old shape shipped a recomputed
        # total under this key — see `_stored_confidence`.
        "confidence": _stored_confidence(alert),
        "provenance": _evidence_provenance(alert, related),
        "contract": contract,
        "related": related,
        "timeline": list(reversed(related))[-8:],
        "related_rules": sorted({r["rule"] for r in related if r.get("rule")}),
    }


@router.get("/ticker/{symbol}")
def ticker_evidence(symbol: str, days: int = 90):
    sym = _first_ticker(symbol).upper()
    conn = db_connection()
    rows = [
        dict(r) for r in conn.execute(
            """
            SELECT id, rule, ticker, severity, headline, created_at,
                       corroborates, corroboration_note
            FROM alerts
            WHERE ticker LIKE ? AND datetime(created_at) >= datetime('now', ?)
            ORDER BY datetime(created_at) DESC LIMIT 40
            """,
            (f"%{sym}%", f"-{days} days"),
        ).fetchall()
    ]
    conn.close()
    return {
        "ticker": sym,
        "signals": rows,
        "rules": sorted({r["rule"] for r in rows if r.get("rule")}),
        "count": len(rows),
    }
