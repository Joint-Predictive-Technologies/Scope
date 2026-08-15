#!/usr/bin/env python3
"""POSITION-SIZING CONTEXT — what a dollar figure is actually worth against the company.

A $2M SBIR grant is a rounding error on a $50B prime and it is the whole company on a $40M
micro-cap, and until now Scope printed both as "$2M". `RULE_11`'s severity ladder is
absolute by design, so the ONE place this asymmetry can be fixed without touching the
detection path is the display layer. That is what this module feeds.

════ WHAT THIS IS NOT ════

⚠️ THIS IS A DISPLAY CACHE AND IT NEVER REACHES THE SCORE. Nothing in `rule_*`,
`RULE_CLUSTER`, `insert_alert`, `enrich_scores` or `rule_10_corroboration` reads
`position_sizing_cache`, and nothing may be made to. The gate's own cap-relative contract
weight (`jpt_common.contract_leg_weight`) already exists, reads `ticker_meta`, and is
deliberately untouched by this file — the two arrive at similar arithmetic from opposite
directions and MUST NOT be wired together. If a future change wants the panel's numbers in
the gate, that is a scoring change and needs its own human-gated pass.

⚠️ IT WRITES TO ITS OWN TABLE AND NOWHERE ELSE. Every reused helper below is called in a
mode that cannot write `ticker_meta` or `issuer_cap`: `_market_cap_core` is handed THIS
module's cache accessors and `shared_write=None`. A display feature warming a cache the
gate reads would change which contracts legs get weighted, which is a scoring side effect
delivered by a UI panel.

════ WHY IT REUSES THE COLLECTOR'S RESOLVER ════

`scripts/rule_reddit_collector._market_cap_core` carries five plausibility guards that this
codebase paid for one at a time — the share-count floor (CLBK's 100 nominal shares), the
two-sided staleness bound (MOBX stale-cheap, THM's `end=2033`), the foreign-private-issuer
check (the TSM ADR mis-scale), the computed-cap band, and the implausible-cache self-heal.
Re-deriving a cap here would mean re-deriving all five, and "several call sites each
re-deriving the same rule from a slightly different projection" is the exact bug class this
repo has paid for repeatedly. So the cap published by this panel is the SAME guarded
number, computed by the same code, stored in a different place.

The consequence is stated rather than hidden: when the guards decline to price a company
this panel says **unavailable**, and it says it for market cap, shares and last close
TOGETHER. Publishing the components of a cap the guards rejected would let a reader
hand-multiply their way to precisely the mis-scale the guards exist to prevent — for a
foreign private issuer, SEC ordinary shares x a US ADR price.

════ WHAT IS AND IS NOT AVAILABLE ════

  market cap          SEC shares outstanding x Yahoo last close, guarded. AVAILABLE.
  shares outstanding  `dei:EntityCommonStockSharesOutstanding` + fallbacks. AVAILABLE.
  public float (USD)  `dei:EntityPublicFloat`. AVAILABLE, but it is an ANNUAL cover-page
                      fact measured on the prior second-quarter close, so it is routinely
                      ~12 months old. It ships with its own date for that reason. Note it
                      is a DOLLAR value, not a share count — it is not "float shares".
  TTM revenue         AVAILABLE. See `_ttm_revenue` for the two accepted bases.
  cash runway         AVAILABLE where the company is BURNING. Cash is an instant fact and
                      operating cash flow is a period fact, so the burn rate is annualised
                      from the period actually reported, never assumed quarterly.
  dilution overhang   ⚠️ **UNAVAILABLE, AND THAT IS A FINDING, NOT A GAP TO FILL LATER.**
                      Three independent reasons, any one of which is disqualifying:
                      (1) Scope ingests no SEC filing text at all — `filings` is the
                          CONGRESSIONAL PTR table, not EDGAR.
                      (2) ATM capacity exists only in S-3/424B5 prose. There is no XBRL
                          concept for "shelf remaining", so it cannot be read structurally.
                      (3) `us-gaap:ClassOfWarrantOrRightOutstanding` looks like the answer
                          and is a trap. It is normally DIMENSIONED per warrant class, and
                          the undimensioned fact companyfacts returns may be one class or
                          the total with nothing distinguishing the two. Measured on ONDS
                          (CIK 0001646188) the newest undimensioned value is 267,857 as of
                          2025-12-31, down from 3,616,071 — a number that would render as
                          a near-zero overhang and cannot be shown to be complete.
                      A partial overhang printed as the overhang is a confident wrong
                      number, which is worse than an empty state. The panel says so.

Rate limits: SEC asks for a declared User-Agent and 10 req/s; a cold ticker costs at most
five SEC requests and one Yahoo request, behind a 24h TTL, and only for tickers a human has
actually opened. Yahoo's chart endpoint is unauthenticated and unmetered but is treated as
best-effort — its `quoteSummary` sibling started answering 401 for every symbol once
already, which is why the cap is not sourced from it.

Usage:
    python3 scripts/position_sizing.py --symbols BA,RTX,NSP        # warm the cache
    python3 scripts/position_sizing.py --symbols BA --force        # ignore the TTL
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection, normalize_ticker            # noqa: E402
from scripts.rule_reddit_collector import (                       # noqa: E402
    _cik_for, _companyfacts_units, _last_close, _market_cap_core, _shares_outstanding,
)

TTL_HOURS = 24

# Revenue is not one concept. A filer reporting under ASC 606 uses the contract-with-
# customer tag; older and mixed filers use `Revenues`; some use the net variant. Order is
# preference, and the FIRST concept with usable rows wins outright — merging rows across
# concepts would silently sum two different definitions of the same quarter.
_REVENUE_CONCEPTS = (
    ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
    ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax"),
    ("us-gaap", "Revenues"),
    ("us-gaap", "SalesRevenueNet"),
)
_CASH_CONCEPT = ("us-gaap", "CashAndCashEquivalentsAtCarryingValue")
_CASH_FALLBACK = ("us-gaap",
                  "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents")
_OCF_CONCEPTS = (
    ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
    ("us-gaap", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
)

# A duration fact is accepted as a full year only inside this band. 10-K periods are not
# exactly 365 days (52/53-week retail years, transition periods), and a fact that is
# neither ~a year nor ~a quarter is something else entirely and is dropped rather than
# guessed at.
_FY_DAYS = (330, 400)
_Q_DAYS = (75, 115)

# How far a 52/53-week filer's "same period last year" may land from the exact calendar
# anniversary. Their quarters end on a WEEKDAY, so a one- or two-day drift is the norm and
# a seven-day slack covers a full week's rotation without reaching a neighbouring quarter,
# which is 13 weeks away. See `_fy_plus_ytd`.
_FISCAL_WEEK_SLACK_DAYS = 7


# ── fact selection ────────────────────────────────────────────────────────────

def _rows(units: dict) -> list:
    """USD fact rows out of an XBRL `units` map.

    Explicitly USD-keyed rather than "whatever key comes first": a filer reporting in both
    USD and a second currency publishes both, and `list(units)[0]` would pick by JSON
    order. There is no FX conversion here on purpose — a cap in dollars beside revenue in
    euros is a wrong ratio that looks right.
    """
    return list(units.get("USD") or [])


def _days(row) -> int | None:
    """Length of a duration fact in days, INCLUSIVE of both endpoints.

    An XBRL period of 2026-01-01..2026-06-30 is a 181-day half-year; the bare date
    subtraction is 180. The `+1` is immaterial to the FY/quarter bands below, which have
    tens of days of slack, but it is NOT immaterial to `cash_runway_months`, which divides
    by this to get a monthly burn rate — a 0.55% error there is a silently wrong number on
    a panel whose whole claim is that its numbers are exact.
    """
    try:
        return (datetime.fromisoformat(row["end"]).date()
                - datetime.fromisoformat(row["start"]).date()).days + 1
    except (TypeError, ValueError, KeyError):
        return None


def _dedupe_periods(rows: list) -> list:
    """One row per (start, end), the newest FILING winning a restatement.

    SEC republishes a prior period every time it is restated or simply carried into a later
    comparative column, so the same quarter appears many times with different `filed` dates
    and occasionally different values (ONDS's 2025 H1 is 10,521,570 as first filed and
    10,522,000 as re-filed in 2026). Taking the newest filing is the restated truth.
    """
    by_period: dict = {}
    for r in rows:
        if not r.get("start") or not r.get("end"):
            continue
        key = (r["start"], r["end"])
        if key not in by_period or (r.get("filed") or "") > (
                by_period[key].get("filed") or ""):
            by_period[key] = r
    return list(by_period.values())


def _minus_one_year(d):
    try:
        return d.replace(year=d.year - 1)
    except ValueError:          # 29 February
        return d.replace(year=d.year - 1, day=28)


def _fy_plus_ytd(periods: list, fy: dict) -> tuple[float, str] | None:
    """TTM as `FY + YTD_this_year - YTD_same_period_last_year`, or None.

    ⚠️ THIS IS THE BASIS THAT ACTUALLY FIRES FOR US FILERS, and the reason the quarterly
    path below almost never does: **nobody reports Q4 as a quarter.** A 10-K publishes the
    full year, so the quarterly facts run Q1, Q2, Q3, Q1, Q2, Q3 with a hole every fourth
    slot. Four "consecutive" quarterly rows therefore span fifteen months, not twelve —
    which is exactly what the span check rejects, and why ONDS and GCTS fell back to a
    year-end figure while their newest quarter was current.

    Every term here is a REPORTED duration fact and the operation is addition and
    subtraction of non-overlapping periods, so the result is exact. It is not an
    annualisation, an extrapolation or a run-rate.

    The two START dates must line up EXACTLY: the interim starts the day after the fiscal
    year ends, and the prior-year comparable starts on the fiscal year's own start date.
    Those are structural and are never approximate.

    ⚠️ THE PRIOR-YEAR END DATE GETS A ±7-DAY WINDOW, AND WITHOUT IT THIS SILENTLY REFUSED
    ~29% OF REAL TICKERS. A 52/53-week filer's quarters land on a weekday, not a calendar
    date, so "the same period last year" ends a day or two off: QCOM's current nine months
    end 2026-06-28 and the prior-year nine months end 2025-06-29. Demanding the exact
    calendar day made every such filer fall back to a year-end `FY` figure up to eleven
    months stale — measured on 10 of 34 sampled tickers (QCOM, AMD, INTC, MU, LRCX, LMT,
    GD, LDOS, TXT, TTMI), which is most of the semiconductor and defence names this panel
    exists to serve.

    ⚠️ THE WINDOW DOES NOT WEAKEN THE ARITHMETIC, AND HERE IS WHY. The three legs telescope:
    `FY(fy_start..fy_end) + interim(fy_end+1..cur_end) - prior(fy_start..prior_end)` leaves
    exactly `prior_end+1 .. cur_end`. So the tolerance does not blur a boundary — it changes
    the LENGTH of the resulting window, and that length is then checked against the same
    330-400 day band every other basis uses. A mismatch that would produce a fourteen-month
    number is still rejected; it is rejected by measuring the result rather than by
    demanding a calendar coincidence that real fiscal calendars do not produce.
    """
    try:
        fy_start, fy_end = fy["start"], datetime.fromisoformat(fy["end"]).date()
    except (TypeError, ValueError, KeyError):
        return None
    next_start = (fy_end + timedelta(days=1)).isoformat()

    interim = [r for r in periods
               if r["start"] == next_start and r["end"] > fy["end"]
               and (d := _days(r)) is not None and d < _FY_DAYS[0]]
    if not interim:
        return None
    cur = max(interim, key=lambda r: r["end"])

    try:
        cur_end = datetime.fromisoformat(cur["end"]).date()
        target = _minus_one_year(cur_end)
    except (TypeError, ValueError):
        return None

    candidates = []
    for r in periods:
        if r["start"] != fy_start:
            continue
        try:
            end = datetime.fromisoformat(r["end"]).date()
        except (TypeError, ValueError):
            continue
        if abs((end - target).days) <= _FISCAL_WEEK_SLACK_DAYS:
            candidates.append((abs((end - target).days), r["end"], r))
    if not candidates:
        return None
    prior = min(candidates, key=lambda c: (c[0], c[1]))[2]

    # The telescoped window the three legs actually describe. Measured, not assumed.
    span = (cur_end - datetime.fromisoformat(prior["end"]).date()).days
    if not (_FY_DAYS[0] <= span <= _FY_DAYS[1]):
        return None

    return (float(fy["val"]) + float(cur["val"]) - float(prior["val"]), cur["end"])


def _four_quarters(periods: list) -> tuple[float, str] | None:
    """TTM as four CONSECUTIVE, NON-OVERLAPPING quarters spanning 330-400 days, or None.

    ⚠️ THE NON-OVERLAP CHECK IS THE WHOLE FUNCTION. XBRL publishes Q2 both as the three
    months to June AND as the six months to June, under the SAME concept, distinguished
    only by `start`. Summing the newest four rows by `end` therefore double-counts a
    quarter and overstates revenue by up to ~50% — and it fails silently, because the
    result is a plausible number of a plausible magnitude. Each quarter is admitted only if
    its `end` is at or before the previously taken quarter's `start`.

    ⚠️ THE SPAN CHECK IS WHAT MAKES THE MISSING Q4 SAFE rather than catastrophic. Without
    it, a US filer's Q2/Q1/Q3/Q2 sequence would sum fifteen months of revenue and publish
    it as a year. See `_fy_plus_ytd`.
    """
    quarters = [r for r in periods
                if (d := _days(r)) is not None and _Q_DAYS[0] <= d <= _Q_DAYS[1]]
    if len(quarters) < 4:
        return None
    picked, cursor = [], None
    for r in sorted(quarters, key=lambda r: r["end"], reverse=True):
        if cursor is not None and r["end"] > cursor:
            continue              # overlaps the quarter already taken
        picked.append(r)
        cursor = r["start"]
        if len(picked) == 4:
            break
    if len(picked) < 4:
        return None
    span = (datetime.fromisoformat(picked[0]["end"]).date()
            - datetime.fromisoformat(picked[-1]["start"]).date()).days
    if not (_FY_DAYS[0] <= span <= _FY_DAYS[1]):
        return None
    return sum(float(r["val"]) for r in picked), picked[0]["end"]


def _ttm_revenue(facts: dict) -> tuple[float, str, str] | None:
    """(revenue, as_of, basis) for the trailing twelve months, or None.

    THREE ACCEPTED BASES, NONE OF THEM AN ESTIMATE:

      `TTM-FY+YTD` FY + this year's interim - last year's matching interim. Exact.
      `TTM-4Q`     four consecutive non-overlapping quarters spanning ~a year. Exact.
      `FY`         a single reported annual fact. Exact, but as stale as the year end.

    ⚠️ EVERY CONCEPT IS EVALUATED AND THE FRESHEST ANSWER WINS — the first version returned
    on the first concept that had any rows, and that shipped a real bug: Boeing's
    `RevenueFromContractWithCustomerExcludingAssessedTax` holds exactly three facts, the
    newest ending **2019-12-31**, while its `Revenues` runs to the current quarter. Because
    the contract-with-customer tag is listed first by preference, BA's "TTM revenue"
    resolved to $76.6B of 2019 — a seven-year-old number, correctly labelled `FY`, sitting
    beside a live market cap. Preference order now only breaks TIES on freshness.

    The basis is stored and displayed beside the number, because "trailing revenue" that is
    really a year-end figure eleven months old is a materially different fact from a true
    TTM, and the reader is the one who should decide whether that matters.
    """
    # Freshness first, then concept preference, then basis preference. The last term only
    # ever decides a tie between two EXACT answers for the same period end — a real filer
    # can publish both a four-quarter run and an FY+YTD reconstruction ending on the same
    # day — and without it `max` would fall through to comparing the VALUES and silently
    # publish whichever happened to be larger.
    basis_pref = {"TTM-FY+YTD": 2, "TTM-4Q": 1, "FY": 0}
    candidates = []
    for rank, concept in enumerate(_REVENUE_CONCEPTS):
        periods = _dedupe_periods(_rows(facts.get(concept) or {}))
        if not periods:
            continue
        annual = [r for r in periods
                  if (d := _days(r)) is not None and _FY_DAYS[0] <= d <= _FY_DAYS[1]]
        if annual:
            fy = max(annual, key=lambda r: (r["end"], r.get("filed") or ""))
            candidates.append((float(fy["val"]), fy["end"], "FY", rank))
            if (ttm := _fy_plus_ytd(periods, fy)):
                candidates.append((ttm[0], ttm[1], "TTM-FY+YTD", rank))
        if (q4 := _four_quarters(periods)):
            candidates.append((q4[0], q4[1], "TTM-4Q", rank))

    if not candidates:
        return None
    value, as_of, basis, _ = max(
        candidates, key=lambda c: (c[1], -c[3], basis_pref[c[2]]))
    return value, as_of, basis


def _instant(facts: dict, concept: tuple) -> tuple[float, str] | None:
    """The newest point-in-time USD fact for one concept."""
    units = facts.get(concept) or {}
    rows = _rows(units)
    if not rows:
        return None
    top = max(rows, key=lambda r: (r["end"], r.get("filed") or ""))
    return float(top["val"]), top["end"]


def _operating_cash_flow(facts: dict) -> tuple[float, str, str] | None:
    """(value, period_start, period_end) for the newest operating-cash-flow period.

    The PERIOD TRAVELS WITH THE VALUE because the burn rate depends on it. Operating cash
    flow is reported year-to-date, so a Q2 10-Q's figure covers six months, a Q3's covers
    nine. Dividing any of them by three because "it came from a 10-Q" is how a runway
    estimate ends up wrong by a factor of three — see `cash_runway_months`.
    """
    for concept in _OCF_CONCEPTS:
        rows = [r for r in _rows(facts.get(concept) or {}) if _days(r)]
        if not rows:
            continue
        top = max(rows, key=lambda r: (r["end"], r.get("filed") or "",
                                       r.get("start") or ""))
        return float(top["val"]), top["start"], top["end"]
    return None


def cash_runway_months(cash: float | None, ocf: float | None,
                       start: str | None, end: str | None) -> float | None:
    """Months of cash at the reported burn rate, or None when the question does not apply.

    None means one of three honest things, and the API distinguishes them: no cash figure,
    no cash-flow figure, or a company that is NOT BURNING. A cash-flow-positive company has
    no runway in this sense, and printing a very large number for one would read as a
    forecast. `>= 0` returns None; the caller labels it.

    ⚠️ ANNUALISED FROM THE PERIOD ACTUALLY REPORTED. Boeing's Q2 figure covers 2026-01-01
    to 2026-06-30 — 181 days, not 90 — so the monthly rate is the period burn over the
    period's own length in months. This is arithmetic on two reported facts and nothing
    else: no trend, no forward assumption, no smoothing.
    """
    if cash is None or ocf is None or ocf >= 0 or not start or not end:
        return None
    days = _days({"start": start, "end": end})       # inclusive — see `_days`
    if not days or days <= 0:
        return None
    monthly_burn = monthly_burn_rate(ocf, start, end)
    if not monthly_burn:
        return None
    return round(cash / monthly_burn, 1)


def monthly_burn_rate(ocf: float | None, start: str | None,
                      end: str | None) -> float | None:
    """Operating cash burn per month over the period actually reported, or None.

    Exposed separately because the panel shows the RATE beside the runway. A runway of
    "193 months" is arithmetically correct for a company whose half-year operating cash
    flow happened to be slightly negative on a working-capital swing, and reads as a
    forecast unless the reader can see it was derived from a $3.2M/month figure over one
    reported half-year. The rate and its period are the disclosure that stops the runway
    from overclaiming.
    """
    if ocf is None or ocf >= 0 or not start or not end:
        return None
    days = _days({"start": start, "end": end})
    if not days or days <= 0:
        return None
    rate = (-ocf) / (days / 30.44)
    return rate if rate > 0 else None


# ── the resolver ──────────────────────────────────────────────────────────────

_SELECT = """SELECT market_cap, fetched_at, cik, cap_updated, shares_outstanding,
                    shares_as_of, last_close, public_float_usd, public_float_as_of,
                    ttm_revenue, ttm_revenue_as_of, ttm_revenue_basis, cash_usd,
                    cash_as_of, operating_cash_flow, ocf_period_start, ocf_period_end,
                    symbol
             FROM position_sizing_cache WHERE symbol = ?"""


# The two independently-failing legs, as column groups. `_CAP_COLS` is everything derived
# from SEC shares x a Yahoo close; `_FACT_COLS` is everything read out of companyfacts.
_CAP_COLS = ("market_cap", "cap_updated", "shares_outstanding", "shares_as_of",
             "last_close")
_FACT_COLS = ("public_float_usd", "public_float_as_of", "ttm_revenue",
              "ttm_revenue_as_of", "ttm_revenue_basis", "cash_usd", "cash_as_of",
              "operating_cash_flow", "ocf_period_start", "ocf_period_end")


def _fresh(row, ttl_hours: int = TTL_HOURS) -> bool:
    if not row or not row["fetched_at"]:
        return False
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(
            row["fetched_at"]).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return False
    return age < timedelta(hours=ttl_hours)


def _write(conn, symbol: str, **f) -> None:
    cols = ("cik", "market_cap", "cap_updated", "shares_outstanding", "shares_as_of",
            "last_close", "public_float_usd", "public_float_as_of", "ttm_revenue",
            "ttm_revenue_as_of", "ttm_revenue_basis", "cash_usd", "cash_as_of",
            "operating_cash_flow", "ocf_period_start", "ocf_period_end", "fetched_at")
    conn.execute(
        f"INSERT INTO position_sizing_cache (symbol, {', '.join(cols)}) "
        f"VALUES ({', '.join('?' * (len(cols) + 1))}) "
        f"ON CONFLICT(symbol) DO UPDATE SET "
        + ", ".join(f"{c}=excluded.{c}" for c in cols),
        (symbol, *(f.get(c) for c in cols)))
    conn.commit()


def resolve(conn, symbol: str, force: bool = False) -> dict | None:
    """Resolve and cache one symbol's position-sizing facts. Returns the stored row.

    ⚠️ THE CAP IS THE GATE ON EVERY PRICED FIELD. `_market_cap_core` is the authority; if
    it declines, this writes a row whose cap, shares and last close are all NULL — see the
    module docstring on why the components cannot be published without it. The fundamentals
    (float, revenue, cash, cash flow) are NOT gated on it: they are reported facts that do
    not depend on a share price, and they stay useful on a company nobody can price.
    """
    symbol = (normalize_ticker(symbol) or symbol or "").upper()
    if not symbol:
        return None

    row = conn.execute(_SELECT, (symbol,)).fetchone()
    if row and _fresh(row) and not force:
        return dict(row)

    now = datetime.now(timezone.utc).isoformat()

    def _cache_read():
        # `_cached_cap` reads row[0] as the cap and row[1] as its timestamp — the column
        # order in `_SELECT` is chosen to satisfy that contract, not by accident.
        return conn.execute(_SELECT, (symbol,)).fetchone()

    captured: dict = {}

    def _cache_write(cap):
        # The core's write hook is used only to CAPTURE its verdict. The row is written
        # once, below, with the fundamentals alongside — two writes would leave a window
        # where a cap is stored beside another ticker's stale fundamentals.
        captured["cap"] = cap

    # ⚠️ THE CORE IS ALWAYS FORCED, AND THAT IS A TTL FIX RATHER THAN AN OPTIMISATION
    # DEFEATED. `_cached_cap` runs on the collector's clock — `CAP_TTL_DAYS`, thirty days —
    # and this table's own clock is 24 hours. Letting the core read its cache meant that
    # once past our TTL the fundamentals refreshed while the cap was served from a row up
    # to thirty days old, and then `cap_updated` was stamped with `now`: a month-old market
    # cap relabelled as resolved today, on a panel whose entire purpose is dating its
    # facts. Control only reaches this line when `_fresh` has already said the row is due,
    # so skipping the core's read is exactly the intent, not a lost cache hit.
    #
    # `shared_read`/`shared_write` are deliberately absent: they are the `issuer_cap`
    # layer, which the gate's cluster surface reads. This module neither reads nor writes
    # it, so the panel can never warm — or be warmed by — a cache the moat consults.
    cap = _market_cap_core(lambda: _cik_for(symbol), (symbol,), lambda: (),
                           _cache_read, _cache_write, force=True)

    # ⚠️ A TRANSPORT FAILURE IS NOT A VERDICT. The core signals one by returning None
    # WITHOUT calling the write hook — a 429 or a dead socket, as distinct from "asked, and
    # cannot price this". The two legs below fail independently and are handled identically
    # at the end of this function: the leg that could not be ASKED keeps its previous
    # answer, and `fetched_at` is left NULL so the next page load retries instead of
    # caching a non-answer for a full TTL.
    cap_degraded = "cap" not in captured

    cik = _cik_for(symbol)
    shares = as_of = close = None
    if cap and not cap_degraded:
        shares_fact = _shares_outstanding(cik) if cik else None
        close = _last_close(symbol)
        shares, as_of = shares_fact if shares_fact else (None, None)
        # RECONCILIATION, NOT DECORATION. These components are re-fetched after the core
        # computed its cap, so a share count filed in between — or a Yahoo close that
        # ticked over — would put a cap on the page that is not the product of the two
        # numbers printed beneath it. Rather than publish an equation that does not
        # balance, the components are dropped and the cap stands alone.
        if not (shares and close) or abs(shares * close - cap) / cap > 0.005:
            shares = as_of = close = None

    # ⚠️ AN EMPTY `companyfacts` FOR A REAL CIK IS ALSO A TRANSPORT FAILURE, NOT A VERDICT
    # OF "this company reports no revenue". `_companyfacts_units` swallows every exception
    # and returns `{}`, so a 429 is indistinguishable from a filer with no facts — and
    # writing NULLs would publish "Revenue unavailable" on Boeing, then cache it for 24h.
    facts = _companyfacts_units(cik) if cik else {}
    facts_degraded = bool(cik) and not facts

    pub_float = _instant(facts, ("dei", "EntityPublicFloat"))
    revenue = _ttm_revenue(facts)
    cash = _instant(facts, _CASH_CONCEPT) or _instant(facts, _CASH_FALLBACK)
    ocf = _operating_cash_flow(facts)

    fields = {
        "cik": cik,
        "market_cap": cap, "cap_updated": now,
        "shares_outstanding": shares, "shares_as_of": as_of, "last_close": close,
        "public_float_usd": pub_float[0] if pub_float else None,
        "public_float_as_of": pub_float[1] if pub_float else None,
        "ttm_revenue": revenue[0] if revenue else None,
        "ttm_revenue_as_of": revenue[1] if revenue else None,
        "ttm_revenue_basis": revenue[2] if revenue else None,
        "cash_usd": cash[0] if cash else None,
        "cash_as_of": cash[1] if cash else None,
        "operating_cash_flow": ocf[0] if ocf else None,
        "ocf_period_start": ocf[1] if ocf else None,
        "ocf_period_end": ocf[2] if ocf else None,
    }

    # Retention, applied per LEG rather than to the whole row: SEC's XBRL endpoints and
    # Yahoo's chart endpoint fail independently, and a Yahoo outage must not also blank a
    # revenue figure that was fetched successfully in the same pass.
    prior = dict(row) if row is not None else {}
    for failed, cols in ((cap_degraded, _CAP_COLS), (facts_degraded, _FACT_COLS)):
        if failed:
            fields.update({c: prior.get(c) for c in cols})

    _write(conn, symbol,
           fetched_at=(None if (cap_degraded or facts_degraded) else now), **fields)

    stored = conn.execute(_SELECT, (symbol,)).fetchone()
    return dict(stored) if stored else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--symbols", required=True, help="Comma-separated tickers")
    ap.add_argument("--force", action="store_true", help="Ignore the TTL")
    args = ap.parse_args()

    conn = db_connection()
    for sym in [s.strip() for s in args.symbols.split(",") if s.strip()]:
        row = resolve(conn, sym, force=args.force)
        if not row:
            print(f"{sym:<8} unresolvable")
            continue
        def _n(key, fmt=",.0f"):
            val = row.get(key)
            return format(val, fmt) if val is not None else "unavailable"

        runway = cash_runway_months(row.get("cash_usd"), row.get("operating_cash_flow"),
                                    row.get("ocf_period_start"), row.get("ocf_period_end"))
        print(f"{sym:<8} cap={_n('market_cap'):>20}  shares={_n('shares_outstanding'):>16}"
              f"  float=${_n('public_float_usd'):>18}"
              f"  ttm_rev={_n('ttm_revenue'):>18} ({row.get('ttm_revenue_basis') or '-'})"
              f"  runway={runway if runway is not None else 'n/a'}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
