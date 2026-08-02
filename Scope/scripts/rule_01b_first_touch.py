#!/usr/bin/env python3
"""
RULE_01B — First Touch
Fires when a member of Congress makes their very first disclosed trade in a
ticker (no prior record for this member+ticker combination in transactions).

Tags format (pipe-separated to avoid comma collisions with member names):
    full_name|tx_type|delay_days|transaction_date|filing_date
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection, normalize_ticker


def _is_above_15k(band: str) -> bool:
    return bool(band) and band not in {"$1,001 - $15,000", "$1k–15k", "$1,001-$15,000"}


def _validity_set(conn) -> set:
    """The authoritative symbol set, CANONICALISED — `tickers.symbol` and nothing else.

    Same set the two repaired resolvers key on (`rule_09_lobbying.py:147`,
    `scripts/rule_11_contracts.py:446`), so "valid" means one thing across the codebase.

    ⚠️ BOTH SIDES ARE NORMALISED, AND A NAIVE MEMBERSHIP TEST IS WRONG WITHOUT IT.
    `tickers` stores class shares, preferreds and warrants in the DASH form — 551 rows
    contain '-' and ZERO contain '.' (BRK-B, ABR-PD, ACHR-WT) — while
    `jpt_common.normalize_ticker` canonicalises '-' to '.', which is the form that
    reaches `alerts.ticker`. So `ticker IN (SELECT symbol FROM tickers)` rejects all 551
    real symbols as "unvalidated". Measured on the 2026-07-28 snapshot, matching the
    normalisation on both sides recovers BRK.B immediately (41 unvalidated -> 40).

    Reuses `normalize_ticker` rather than reimplementing it: a second canonicaliser here
    would be free to drift from the one the gate's keys are written with.
    """
    return {normalize_ticker(r["symbol"])
            for r in conn.execute("SELECT symbol FROM tickers")
            if r["symbol"]}


def run(emit: bool = False, dry_run: bool = False) -> None:
    import time as _time
    _t0 = _time.time()
    conn = db_connection()

    rows = conn.execute("""
        SELECT
            t.id,
            t.member_id,
            t.raw_ticker_string  AS ticker,
            t.transaction_type,
            t.amount_band,
            t.transaction_date,
            t.filing_date,
            CAST(julianday(t.filing_date) - julianday(t.transaction_date) AS INTEGER) AS filing_delay,
            m.full_name,
            m.party,
            m.state
        FROM transactions t
        JOIN members m ON t.member_id = m.bioguide_id
        WHERE t.raw_ticker_string IS NOT NULL
          AND trim(t.raw_ticker_string) != ''
          AND t.member_id IS NOT NULL
          AND date(t.transaction_date) >= date('now', '-90 days')
          -- A trade whose own date is unknown CANNOT be proven first, so it is
          -- not eligible to carry the "no prior disclosed trade" claim. The
          -- window above already excludes NULLs today; this is stated
          -- separately because the window basis is a queued change and the
          -- honesty guarantee must not depend on it.
          AND date(t.transaction_date) IS NOT NULL
          -- FIRST = CHRONOLOGICALLY EARLIEST, NOT LOWEST id.
          -- This was `t2.id < t.id`, and ingestion batches do not arrive in
          -- trade-date order: a later batch routinely carries older trades. On
          -- the 2026-07-28 snapshot 780 member+ticker pairs had a lowest-id row
          -- that was not the earliest, and 39/192 stored RULE_01B alerts (20.3%)
          -- therefore asserted "no prior disclosed trade" about a member who had
          -- one — Gottheimer/ABT cited 2026-05-27 (id 40206) against a true
          -- earliest of 2025-01-28 (id 43602). RULE_01B is a live gate leg (the
          -- `congressional` instrument), so that is a false corroborating leg,
          -- not a cosmetic headline error.
          AND NOT EXISTS (
              SELECT 1 FROM transactions t2
              WHERE t2.member_id = t.member_id
                AND t2.raw_ticker_string = t.raw_ticker_string
                AND t2.id <> t.id
                AND (
                    -- An undated SIBLING is the live hazard: NULL fails every
                    -- `<` comparison silently, so without these two arms a
                    -- later trade would still claim first-touch whenever the
                    -- genuinely-earlier row happens to have no date. Unknown
                    -- order cannot be ruled out, so it blocks the claim.
                       t2.transaction_date IS NULL
                    OR date(t2.transaction_date) IS NULL
                    OR date(t2.transaction_date) <  date(t.transaction_date)
                    -- Same date: deterministic tiebreak on the lower id, so
                    -- exactly one row of a same-day pair can ever be "first".
                    OR (date(t2.transaction_date) = date(t.transaction_date)
                        AND t2.id < t.id)
                )
          )
        ORDER BY t.transaction_date DESC
        LIMIT 500
    """).fetchall()

    print(f"[RULE_01B] {len(rows)} first-touch transactions found")
    emitted = 0
    unvalidated = 0
    valid_symbols = _validity_set(conn)
    # ⚠️ AN EMPTY VALIDITY SET IS AN INFRASTRUCTURE FAILURE, NOT 100% ARTIFACTS.
    # If `tickers` is empty or unreadable, every symbol fails validation and RULE_01B
    # goes ENTIRELY dark as a gate leg. That is the correct fail-closed direction — a
    # missing validity set must never wave everything through — but it must not happen
    # SILENTLY. Say so loudly here and in the activity_log note.
    if not valid_symbols:
        print("[RULE_01B] ⚠️  CRITICAL: `tickers` is EMPTY — the validity set could not be "
              "built. Every alert this run will be flagged unverified and excluded from "
              "corroboration. This is a fail-closed default, not a verdict about the data.")

    for r in rows:
        ticker     = r["ticker"]
        member_id  = r["member_id"]
        name       = r["full_name"]
        party      = r["party"] or "?"
        state      = r["state"] or "?"
        tx_type    = (r["transaction_type"] or "trade").replace("_", " ")
        amount     = r["amount_band"] or "undisclosed amount"
        delay      = r["filing_delay"]
        tx_date    = r["transaction_date"] or ""
        filed_date = r["filing_date"] or ""

        # ── ONLY A VALIDATED TICKER MAY BECOME A CORROBORATION KEY ──────────────
        # RULE_01B writes the RAW parsed string from the PTR, and the gate groups
        # corroboration candidates by `alerts.ticker` — so an unvalidated string is a
        # live convergence KEY, not a cosmetic label. On the 2026-07-28 snapshot 41 of
        # 192 alerts (21.4%) carried a ticker absent from `tickers`, 22 of them HIGH,
        # and `NY` spanned TWO members (C001123, P000608): a cross-member "convergence"
        # on a string that is not a company.
        #
        # THERE IS DELIBERATELY NO RESOLUTION BRANCH. An unvalidated string is never
        # mapped to a near symbol — that is the difflib fabrication removed from RULE_09
        # and RULE_11. Unresolved stays unresolved.
        #
        # ⚠️ ABSENT FROM `tickers` IS NOT "FAKE", AND THE SPLIT CANNOT BE GUESSED.
        # `tickers` is measurably incomplete (VOO, RYCEY, TPH, CTRA, FAS are all missing),
        # so roughly half the unvalidated strings are REAL securities it simply does not
        # carry and half are PDF parse artifacts. A shape rule does not separate them:
        # 40 of the 41 match ^[A-Z]{1,5}$, and the one that does NOT — BRK.B — is the
        # real one. So nothing is deleted here. The alert is still emitted and the raw
        # string is still recoverable; it is only barred from being a corroboration key,
        # and flagged for a human to triage into `tickers`.
        norm = normalize_ticker(ticker)
        is_valid = norm in valid_symbols
        # The gate's candidate query already filters `ticker IS NOT NULL AND ticker != ''`
        # (rule_10_corroboration.py:181), so a blank key is invisible to corroboration
        # WITHOUT touching the gate and without signing RULE_01B (an explicit
        # `corroborates=0` would be ignored: alert_corroborates returns True
        # unconditionally for any rule outside SIGNED_RULES, and RULE_01B may not be
        # signed until its direction defect is fixed).
        # Store the CANONICAL form, not the raw one. `is_valid` is decided on the
        # normalised string, so storing the raw would let `$ABT`, `brk-b` or ` ABT `
        # validate and then land in the gate's candidate set under a key that is NOT in
        # `tickers` — "only a validated ticker becomes a corroboration key" would be
        # literally false for them. Identity on all 1369 raw strings in the corpus today
        # (verified), so this changes nothing now and closes the hole permanently.
        stored_ticker = norm if is_valid else ""

        # ⚠️ ONE DEDUP PREDICATE FOR BOTH BRANCHES — A FORCED CONSEQUENCE OF THE BLANK
        # KEY, NOT AN INCIDENTAL EDIT. The key is (rule, ticker, member_id); with `ticker`
        # blank every unvalidated alert for a member collapses onto ONE slot (measured:
        # 11 of the 13 affected members hold more than one, P000608 holds 11, so 41 alerts
        # would dedup to ~13). The RAW string is the real identity, so all three arms
        # below key on it:
        #   ticker = norm  — rows written by this code for a valid symbol
        #   ticker = raw   — LEGACY rows, written before this fix, which still hold the
        #                    raw string in the column. This is what stops the whole
        #                    stored corpus from re-emitting as duplicates.
        #   tags suffix    — rows written by this code for an UNVALIDATED symbol, whose
        #                    raw string is the appended 6th tags field.
        #
        # ⚠️ THE SUFFIX TEST IS AN EXACT COMPARE, NOT `LIKE`, AND THAT IS LOAD-BEARING.
        # This branch exists precisely to handle arbitrary PDF-parse garbage, and `_` and
        # `%` are LIKE metacharacters. `tags LIKE '%|' || ?` silently swallowed a distinct
        # alert whenever the raw string contained one — seeding `AXB` then `A_B` for one
        # member emitted ONE alert, order-dependent, with no log line and no counter. No
        # such string exists in the corpus today (0 of 1369), so nothing was lost, but it
        # was a live DATA-LOSS path. Found by a verification pass.
        exists = conn.execute(
            "SELECT 1 FROM alerts WHERE rule='RULE_01B' AND member_id=? "
            "  AND (ticker = ? OR ticker = ? "
            "       OR substr(tags, -(length(?) + 1)) = '|' || ?)",
            (member_id, norm, ticker, ticker, ticker),
        ).fetchone()
        if exists:
            continue

        severity = "HIGH" if _is_above_15k(amount) else "MEDIUM"
        headline = f"First Touch — {name} opens new position in {ticker}"
        detail = (
            f"{name} ({party}-{state}) has no prior disclosed trade in {ticker}. "
            f"Transaction: {tx_type}, {amount}"
            + (f", filed {delay}d after transaction." if delay is not None else ".")
        )
        if not is_valid:
            headline += " (UNVERIFIED SYMBOL)"
            detail += (
                f" ⚠️ UNVERIFIED SYMBOL: \"{ticker}\" is not in the authoritative `tickers`"
                f" set, so it is excluded from corroboration and cannot complete a"
                f" convergence. It is NOT resolved to a similar symbol. This is either a"
                f" filing-parse artifact or a real security missing from `tickers` —"
                f" the two are not distinguishable automatically, so it is flagged for"
                f" review rather than discarded."
            )
        # The raw string is APPENDED as field 5. Append, never prepend: the chronology
        # remap reads `tags.split("|")[3]` for the transaction date, so fields 0-4 must
        # keep their positions. Written for valid and invalid alike, so the field's
        # meaning does not depend on the verdict.
        tags = (f"{name}|{tx_type}|{delay if delay is not None else '?'}"
                f"|{tx_date}|{filed_date}|{ticker}")

        flag = "" if is_valid else " [UNVERIFIED->no corroboration]"
        print(f"[RULE_01B] {'[dry]' if dry_run else '[emit]'} {ticker} — {name} "
              f"({severity}){flag}")
        if not is_valid:
            unvalidated += 1

        if not dry_run and emit:
            conn.execute(
                """INSERT INTO alerts (rule, headline, severity, tags, ticker, member_id,
                                       detail, lifecycle_stage)
                   VALUES ('RULE_01B', ?, ?, ?, ?, ?, ?, ?)""",
                (headline, severity, tags, stored_ticker, member_id, detail,
                 "created" if is_valid else "review"),
            )
            conn.commit()
            emitted += 1

    print(f"[RULE_01B] Done — {emitted} new alerts emitted "
          f"({unvalidated} with an unverified symbol, excluded from corroboration)")
    conn.close()
    from jpt_common import record_activity
    # ⚠️ THE EMPTY-TABLE CHECK ABOVE IS NOT ENOUGH ON ITS OWN.
    # It only fires on an EXACTLY empty `tickers`. A table truncated to a handful of rows
    # by a botched ingest darkens ~99% of the leg and would look, from the outside,
    # exactly like a run in which every symbol genuinely was an artifact. So alarm on the
    # RATIO too: a healthy run is a few percent unverified (41/192 = 21% on the worst
    # stored snapshot), never most of it.
    note = f"unverified_symbols={unvalidated}/{len(rows)} validity_set={len(valid_symbols)}"
    if not valid_symbols:
        note += " CRITICAL:tickers_table_empty_validity_set_unavailable"
    elif rows and unvalidated / len(rows) > 0.5:
        note += " CRITICAL:majority_of_symbols_unverified_suspect_validity_set"
        print(f"[RULE_01B] ⚠️  CRITICAL: {unvalidated}/{len(rows)} symbols failed validation "
              f"against a {len(valid_symbols)}-symbol `tickers` set. That is not a normal "
              f"artifact rate — suspect a truncated or stale validity set, not the data.")
    record_activity("RULE_01B", scanned=len(rows), flagged=len(rows), emitted=emitted,
                    duration_seconds=round(_time.time() - _t0, 2), notes=note)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="RULE_01B — First-touch congressional trades")
    p.add_argument("--emit-alerts", action="store_true")
    p.add_argument("--dry-run",     action="store_true")
    args = p.parse_args()
    run(emit=args.emit_alerts, dry_run=args.dry_run)
