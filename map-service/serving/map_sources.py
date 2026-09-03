#!/usr/bin/env python3
"""Block 11 — the source registry for the map export.

One place that answers, per source: what it is, what its coverage frame actually
is, and whether an independent verifier pass has run on it.

────────────────────────────────────────────────────────────────────────────────
--- what this file may and may NOT assert ---

🔴 A PROCESS FACT IS DECLARED HERE.  A DATA FACT IS NOT.

`verification` says whether a verifier pass has run and passed.  That is a fact
about sessions, it is not derivable from `osint.db`, and a human maintains it.

`open_defects` is NOT here, and the first version of this file got that wrong:
it hardcoded the string *"ONE DEFECT REMAINS LIVE: county 35013 is stored as
'DoÃ±a Ana County, New Mexico'"*.  A concurrent session repaired that row roughly
an hour later, and the literal went on asserting a defect that no longer existed
— on a surface whose entire purpose is not doing that.  Defects are measured
against the live database at export time by `export_map.scan_defects()`, so the
claim expires when the defect does.

--- the coverage frame ---

The map's grammar has three states and two of them are dark: `no-coverage` (never
checked) and `no-signal` (checked, nothing there).  Writing `no-signal` for a
place asserts a sweep.  **None of the five sources below performs one**, and
each fails to for a different, measured reason recorded in `frame_evidence`.

🔴 AND THE FIFTH AND FOURTH ARE NOT LIKE THE FIRST THREE.  The three original
sources cannot sweep IN PRINCIPLE: each enumerates entities that turn out to have
a place, so a county they miss was never asked about.  **MSHA is different and the
difference must not be collapsed into theirs.**  Its registry is compelled by
federal law and covers all 55 state/territory codes and 2,986 counties, so a
county with no live metal mine WAS asked about.  `sweeps_geography` is still
False, but for a narrower, measured reason — the load's `PRIMARY_CANVASS='Metal'`
predicate is not the same set as "metal mining", and **156 counties hold a live
MSHA registration this slice cannot see**.  Written out in full in that source's
`frame_evidence`.

⚠️ AND THE FIRST VERSION OF THIS FILE UNDERSTATED THAT GAP BY ~65×, which matters
because it invited exactly the wrong follow-up.  It recorded only the 4
secondary-canvass mines and their 2 unlit counties, and said a future session that
"closes the canvass gap" could legitimately revisit the boolean.  An independent
verifier found the other 261: live mines with a BLANK canvass, class not yet coded
by MSHA, in 169 counties of which 155 are unlit.  A session that fixed the 4 named
mines and then flipped this boolean would have been acting on a number that was
wrong by two orders of magnitude — so the size of the gap is recorded here, not
just its existence.

⚠️ TWO FURTHER BLOCKERS WOULD REMAIN EVEN IF THE CANVASS GAP WERE CLOSED, and they
are recorded here so that nobody flips the boolean on the strength of the census
argument alone:
  1. THE EXPORT HAS NO COUNTY UNIVERSE.  `entities` holds 1,469 county/county-
     equivalent rows out of ~3,144 — and it holds them because SOME source reached
     them, overwhelmingly PatentsView (1,334 of the 1,469).  Emitting `no-signal`
     only for counties that happen to be in the graph would make the dark-vs-absent
     split a function of the PATENT layer's reach rather than MSHA's, on a map whose
     grammar exists to keep those two facts apart.
  2. THE PREDICATE IS FAR NARROWER THAN THE WORD "COMMODITY".  These two sources
     see live metal-canvassed mines and uranium ISR/mill facilities.  They do not
     see coal (35,614 rows in the same file), sand and gravel (30,459), stone
     (15,998), non-metal (4,615), the 282 rows MSHA has not yet canvassed at all,
     oil and gas, or any exploration-stage property — categories A1-A6 of
     `map-dot-taxonomy`, which have no source at all yet.

⚠️ NARROWER THAN THE CLAIM FIRST WRITTEN HERE, WHICH THE VERIFIER OVERTURNED.  It
read "no source in this corpus sweeps geography".  That is false:
`loader/load_phase0.py:load_coverage()` enumerates every `state` location entity
and writes `no_signal` where no `congressional_transaction` event points at it.
It is a real place-enumerating writer.  The true statement is the narrower one —
**none of the five sources THIS EXPORT USES sweeps**, and this export never
reads `coverage_log` at all.  ⚠️ MSHA's registry is a SECOND real place-enumerating
source in this corpus, found by Stage 1 of the commodity wiring; it is written up
under `commodity_msha` below rather than left as an absence.

⚠️ And that loader is not a route to reinstating `no-signal` either, for a
reason worth recording: it last ran 2026-08-13 over the 36 state entities that
then existed, all of which had trades. The 9,963 congressional transactions have
since been re-pointed to `congressional_district` precision — only 4 still name a
state — so re-running it today would write `no_signal` for **50 of 53 states**
that do have congressional trade activity, at district resolution, in the same
database.  Its output would be a false negative, not a sweep.
"""
from __future__ import annotations

from dataclasses import dataclass


# entities.entity_type -> the map's node vocabulary.
# 🔴 Only types the corpus actually contains.  The prototype's legend advertised
# `contract`, `event` and `facility`; the graph holds ZERO entities of each
# (contracts exist as `government_contract` EDGES, events as rows in `events`,
# facilities not at all).  A legend promising node classes that cannot appear is
# a quiet overclaim, so they are dropped and `location`/`research` — 15,384 and
# 1,178 real rows — take their place.
NODE_TYPES = {
    "company": "company",
    "person": "person",
    "patent": "patent",
    "government_agency": "agency",
    "location": "location",
    "research": "research",
    # 🔴 `asset` — 338 REAL ROWS, AND ITS ABSENCE WAS A SILENT DROP, NOT A GAP.
    # 314 MSHA mines and 24 EIA-851A uranium facilities are `entity_type='asset'`,
    # and they carry 730 `ownership`/`contractor` edges in the SHARED `edges`
    # table.  Those edges were therefore already counted in `degree` — and then
    # every asset neighbour hit `node() -> None` and was dropped by the
    # `if on is None: continue` in `neighborhood()`.  So clicking a mine's holder
    # showed a degree that included its mines and a neighbour list that did not.
    # A node type missing from this map is not "unsupported"; it is a disclosed
    # count that silently disagrees with what is drawn.
    "asset": "asset",
    # 🔴 `permit` — 6,950 REAL ROWS, ADDED BEFORE THE DROP RATHER THAN AFTER IT.
    # The `asset` entry above records a bug found only once mines were already
    # shipping: a type missing from this map hits `node() -> None` in
    # `graph_api.neighborhood()` and is dropped from the neighbour list *after*
    # being counted into `degree`.  North Dakota's load writes 6,944 `permit`
    # entities (a permitted well where NO HOLE WAS MADE) and Kansas 6, and every
    # one of them would have reproduced that bug exactly.  Measured before wiring,
    # not after: `select entity_type, count(*) from entities` -> permit 6,950.
    # ⚠️ A permit is NOT an asset and is deliberately not folded into one — ND's
    # own status guide is what separates them, and the map must not assert a hole
    # exists where the registry says one does not.
    "permit": "permit",
}


@dataclass(frozen=True)
class Source:
    id: str
    label: str
    frame: str
    # 🔴 does this source enumerate PLACES (as opposed to entities that happen to
    # have a place)?  Only a True here can ever produce `no-signal`.
    sweeps_geography: bool
    frame_evidence: str
    # 'hardened' — an independent verifier pass has run AND passed
    # 'pending'  — real data, no verifier pass yet
    verification: str
    verifier_pass: str | None          # the session that ran it
    verification_note: str
    # entities.source_system values this source is responsible for, so a defect
    # found in the data can be attributed to a layer rather than to the map
    source_systems: tuple[str, ...]
    tier: int
    evidence: str                      # 'direct' | 'inferred'
    # 🔴 A SWEEP IS NOT NECESSARILY GLOBAL, AND THE FIRST ONE THIS MAP HOLDS IS NOT.
    # `sweeps_geography` alone was a whole-map claim, which is the only shape the
    # first four sources needed: they cannot sweep anywhere, so False said it all.
    # A STATE regulator is different — NDIC's well registry enumerates places
    # completely inside North Dakota and says NOTHING about Texas.  Reading a bare
    # True as a licence to emit `no-signal` in a state the source has never seen
    # would be a far worse claim than the hatch it replaces.
    # So a sweeping source MUST name the FIPS state codes it sweeps, and
    # `no-signal` is confined to them.  Empty tuple = sweeps nowhere.
    sweep_scope: tuple[str, ...] = ()


SOURCES: dict[str, Source] = {
    "contract": Source(
        id="contract",
        label="Contract place of performance",
        frame="171 Scope-selected contracts; 106 with a resolvable award id",
        sweeps_geography=False,
        frame_evidence=(
            "loader/load_place_of_performance.py resolves place of performance for the "
            "contracts already in Scope's own contract table — '65 of 171 contracts are in "
            "that bucket and stay there'.  It is a lookup over a selected contract list, "
            "not an enumeration of counties.  A county with no award here was never asked "
            "about."
        ),
        verification="hardened",
        verifier_pass="SESSION-2026-08-15-county-place-of-performance-resumed",
        verification_note="Block 2/4, production. Its verifier pass upheld 7 of 8 claims and "
                          "overturned the one that mattered (fabricated place names), which "
                          "was then fixed. Cross-validated against patent assignee address on "
                          "8 of 9 companies carrying both.",
        source_systems=("scope",),
        tier=1,
        evidence="direct",
    ),
    "patent": Source(
        id="patent",
        label="Patent address (assignee / inventor)",
        frame="576,452 patent mentions for companies already in the graph",
        sweeps_geography=False,
        frame_evidence=(
            "The patent-geocoding session states it outright: 'This corpus is company-"
            "selected, not a geographic census. Writing no_signal for a place would assert "
            "a geographic sweep that never happened.'  It wrote zero coverage_log rows for "
            "exactly that reason, and this export honours that decision rather than "
            "quietly reversing it one layer up."
        ),
        verification="hardened",
        verifier_pass="SESSION-2026-09-01-patent-geocoding-verify",
        verification_note="Verifier pass PASSED: all 576,452 rows re-derived from source and "
                          "from live BigQuery, identical; 0 invented centroids, 159 coarse "
                          "coordinates actively refused. It also overturned four of the "
                          "loading session's claims.",
        source_systems=("patentsview",),
        tier=1,
        evidence="direct",
    ),
    "demand": Source(
        id="demand",
        label="Demand relevance",
        frame="3 manually-authored demand signals; 181 relevance edges",
        sweeps_geography=False,
        frame_evidence=(
            "demand_relevance is an entity-level judgement — 'this company's patents sit in "
            "CPC classes this demand needs'.  It carries no geography of its own; it reaches "
            "the map only through the located facts of the entity it names.  There is no "
            "sense in which it checks a county."
        ),
        verification="hardened",
        verifier_pass="SESSION-2026-09-01-demand-ranking-join-and-fix",
        verification_note="Block 8. report_demand_ranking.py: 22 tests, 16 of 16 mutations "
                          "caught, non-regression proven on all three signals.",
        source_systems=("scope",),
        tier=2,
        evidence="inferred",
    ),
    # ══════════════════════════════════════════════════════════════════════════
    # 🔴 TWO ENTRIES, NOT ONE "COMMODITY" SOURCE — AND THAT IS NOT A STYLE CHOICE.
    #
    # `loader/load_mineral_sites.py` and `loader/load_eia_facilities.py` carry one
    # shared, verifier-upheld invariant: MSHA AND EIA ARE NEVER MERGED.  They
    # genuinely disagree about the same physical sites — Smith Ranch-Highland is
    # `Abandoned / BHP Billiton / 2002` in MSHA and `Operating / Cameco / 2025` in
    # EIA — and 5 of the 6 facilities EIA calls Operating are Abandoned in MSHA.
    # `White Mesa Mill` deliberately exists twice, once under each anchor.
    #
    # A single `commodity` source would put both behind one confidence, one tier
    # and one MIN, which is precisely the laundering the never-merge rule exists to
    # prevent: the county's number would be the minimum of two sources that are
    # allowed to contradict each other.  So each gets its own registry row, its own
    # signal in the county's array, and its own frame.
    # ══════════════════════════════════════════════════════════════════════════
    "commodity_msha": Source(
        id="commodity_msha",
        label="Live metal mine (MSHA registry)",
        frame="314 live metal mines in 131 counties across 33 states — MSHA's whole "
              "registry filtered to PRIMARY_CANVASS='Metal' and a non-abandoned status; "
              "156 further counties hold a live registration the filter cannot see",
        # 🔴 FALSE — BUT FOR A REASON THAT IS NOT THE OTHER THREE SOURCES' REASON,
        # AND THE DIFFERENCE IS RECORDED RATHER THAN COLLAPSED.  See frame_evidence.
        sweeps_geography=False,
        frame_evidence=(
            "⚠️ THIS SOURCE IS NOT LIKE THE OTHER THREE AND THE FINDING IS NARROWER THAN "
            "THEIRS.  MSHA's Mines.txt IS a geographic sweep: registration is compelled by "
            "the Mine Act, and the file holds 91,985 mines spanning all 55 US state and "
            "territory codes and 2,986 distinct counties.  A county with no live metal mine "
            "WAS asked about, which is exactly what a patent corpus or a contract list can "
            "never say.  `no-signal` is refused anyway because the LOADED SLICE is not that "
            "sweep, and the gap is measured in the source file itself, in two parts.  "
            "🔴 THE LARGE ONE: 261 live mines carry a BLANK PRIMARY_CANVASS — 257 of them "
            "status 'New Mine' — with PRIMARY_SIC and SECONDARY_CANVASS blank too.  MSHA has "
            "not yet coded their commodity class, so it is UNKNOWN, not non-metal, and "
            "PRIMARY_CANVASS='Metal' excludes every one.  They sit in 169 counties across 41 "
            "states, and 155 of those counties hold no loaded site at all — 'Santa Cruz "
            "Copper Project' (AZ Pinal), 'South Railroad Mine' (NV Elko), 'Brook Mine' (WY "
            "Sheridan) among them.  ⚠️ THE SMALL ONE, and it was the only one recorded here "
            "at first: 4 live mines carry SECONDARY_CANVASS='Metal' with a 'Gold Ore' SIC "
            "under a SandAndGravel or Nonmetal primary canvass, adding 2 more unlit counties "
            "— 08029 Delta, Colorado and 35051 Sierra, New Mexico.  156 unlit counties in "
            "total hold a live MSHA registration this slice cannot see.  Emitting `no-signal` "
            "for any of them would assert 'checked, no metal mining here' over a live federal "
            "registration named in the very file the claim would rest on."
        ),
        verification="hardened",
        verifier_pass="SESSION-2026-09-01-mineral-site-load",
        verification_note="Verifier pass upheld all five data claims and adopted 3 defects. "
                          "The county anchor is built from the two-letter STATE field, not "
                          "BOM_STATE_CD (32 of 33 states mismatch and fail by producing "
                          "PLAUSIBLE false county matches), and was validated on names: 59 of "
                          "59 reused counties agree with the graph's own display_name.",
        source_systems=("msha",),
        tier=1,
        evidence="direct",
    ),
    # 🔴 THE FIRST SWEEPING SOURCE THIS MAP HAS EVER HELD, AND THE ARGUMENT IS THE
    # OPPOSITE OF MSHA'S.  Measured fresh from ND's own registry, not inherited.
    "oil_gas_nd": Source(
        id="oil_gas_nd",
        label="Oil or gas well (NDIC registry)",
        frame="43,871 wells across 52 of North Dakota's 53 counties — the ENTIRE "
              "published NDIC well layer, unfiltered; 36,917 carry a coordinate and "
              "6,954 carry no date the event carrier can hold and are therefore "
              "loaded but not located",
        # 🔴 TRUE — THE FIRST ONE.  Scoped to FIPS 38 and nowhere else.
        sweeps_geography=True,
        sweep_scope=("38",),
        frame_evidence=(
            "🔴 THIS SOURCE SWEEPS AND MSHA DOES NOT, AND THE DIFFERENCE IS THE "
            "ABSENCE OF A CANVASS GAP.  MSHA's registry is nationwide but the LOADED "
            "SLICE is `PRIMARY_CANVASS='Metal'`, and 261 live mines in 155 otherwise "
            "unlit counties fall outside it — so that slice cannot say 'checked'.  "
            "North Dakota has no such filter: the live FeatureServer answers "
            "`where=1=1` with COUNT 43,871, exactly the number loaded, and the layer "
            "carries no definitionExpression.  The loaded slice IS the registry.  "
            "⭐ AND THE REGISTRY RECORDS ATTEMPTS, NOT ONLY SUCCESSES, which is what "
            "makes an empty county a FINDING rather than a gap: 5,737 cancelled "
            "permits (PNC), 599 permitted-but-undrilled (LOC), 22 expired, 20 "
            "suspended and 6,347 dry holes — 12,725 rows, 29.0% of the file, are "
            "failures.  A county with no row is a county where nobody has even "
            "APPLIED, and NDIC would hold the application if they had.  "
            "🔴 EXACTLY ONE COUNTY IS AFFECTED TODAY: 38097 Traill, absent from the "
            "live layer's own 52 distinct County values and from all 43,871 rows.  "
            "It is the Red River Valley, off the Williston Basin, and it currently "
            "renders HATCHED — 'never checked' — which this registry falsifies.  "
            "⚠️ THE SWEEP IS BOUNDED AND THE BOUND IS LOAD-BEARING.  NDIC regulates "
            "North Dakota.  It has not checked Texas, and `sweep_scope` confines "
            "`no-signal` to FIPS 38 so that no reader is ever told a county outside "
            "North Dakota was checked by a North Dakota regulator.  "
            "⚠️ NOT ARGUED FROM STATUTE: the completeness claim rests on the measured "
            "identity of the pull and the live count, and on NDIC's own layer "
            "description, NOT on a reading of NDCC ch. 38-08, which was not opened."
        ),
        verification="pending",
        verifier_pass=None,
        verification_note="Loaded and verifier-passed at the LOADER layer in "
                          "SESSION-2026-09-03-nd-oilgas-load (three defects found in "
                          "applied data and corrected).  The MAP wiring is this "
                          "session's and its verifier pass is recorded there.",
        source_systems=("ndic",),
        tier=1,
        evidence="direct",
    ),
    "commodity_eia": Source(
        id="commodity_eia",
        label="Uranium ISR plant or mill (EIA-851A)",
        frame="24 facilities — every data row of published Tables 4 and 5 (20 in-situ "
              "recovery plants, 4 mills/heap leach); 15 counties + 2 state-tier footprints",
        sweeps_geography=False,
        frame_evidence=(
            "Form EIA-851A is a mandatory annual survey and Tables 4-5 enumerate their "
            "facility class completely — all 24 published rows are loaded, including the "
            "planned and undeveloped ones ('Jab and Antelope', Undeveloped), so the load "
            "drops nothing.  But the class is narrow in a way that forbids a dark dot: "
            "Tables 4-5 enumerate PROCESSING FACILITIES AND NO CONVENTIONAL MINES — measured "
            "in SESSION-2026-09-01-mineral-anchors-m033, 7 of the 8 MSHA non-abandoned "
            "uranium records have no EIA counterpart at all for exactly that reason.  A "
            "county with no ISR plant may hold a conventional uranium mine, and this source "
            "cannot see it.  'No uranium facility of these two kinds' is the only claim these "
            "rows support, and it is not the claim a reader takes from an empty map."
        ),
        verification="hardened",
        verifier_pass="SESSION-2026-09-01-commodity-holder-resolution",
        verification_note="Verifier pass PROVEN: it scanned every row of every table for a "
                          "cross-reference to MSHA and found 0 bridges (White Mesa Mill is "
                          "dual and unmerged, as required), and re-derived all 21 county "
                          "GEOIDs from the Census gazetteer.  EIA publishes no coordinates "
                          "at all; no centroid is derived for any facility.",
        source_systems=("eia",),
        tier=1,
        evidence="direct",
    ),
}

# 🔴 The invariant this module exists to hold, asserted at import rather than
# documented and hoped for.
#
# ⚠️ IT USED TO BE ABSOLUTE AND IT NO LONGER IS.  Until `oil_gas_nd` every source
# swept nowhere, so one boolean carried the whole rule.  What must still never
# happen is an UNBOUNDED sweep — a source claiming to have checked everywhere —
# and that is what this now asserts.  A scoped sweep is a different, weaker and
# checkable claim, and `SWEEP_SCOPES` is where its bounds live.
NO_SOURCE_SWEEPS_UNBOUNDED = not any(
    s.sweeps_geography and not s.sweep_scope for s in SOURCES.values())
assert NO_SOURCE_SWEEPS_UNBOUNDED, (
    "a source claims to sweep geography with no `sweep_scope`; `no-signal` would "
    "escape the jurisdiction that was actually checked")

# source id -> the FIPS state codes it sweeps.  Only these may produce `no-signal`,
# and only inside these states.
SWEEP_SCOPES: dict[str, tuple[str, ...]] = {
    sid: s.sweep_scope for sid, s in SOURCES.items()
    if s.sweeps_geography and s.sweep_scope
}
# every FIPS state any source sweeps, for the frontend's per-county decision
SWEPT_STATES: frozenset[str] = frozenset(
    st for scope in SWEEP_SCOPES.values() for st in scope)

# retained under its old name for callers that ask the old question
NO_SOURCE_SWEEPS = not any(s.sweeps_geography for s in SOURCES.values())

# 🔴 WHICH SOURCES ARE INDEPENDENT OBSERVERS OF EACH OTHER, AND WHICH ARE NOT.
# `commodity_msha` and `commodity_eia` are two SOURCES and one FAMILY: they
# describe overlapping physical sites deliberately (`White Mesa Mill` is loaded
# twice, once under each anchor, and must never be merged), so a county lit by
# both is one fact observed twice, not two facts agreeing.  Anything counting
# convergence must collapse a family first or it inflates its own headline — the
# exact shape of the `district_county_overlap` double-count the precedent session
# caught, one layer over.
#
# ⚠️ EXPLICIT, NOT DERIVED FROM THE ID STRING.  A `sid.split("_")[0]` would work
# today and break silently the first time a source id contains an underscore for
# any other reason.
FAMILY: dict[str, str] = {
    "contract": "contract",
    "patent": "patent",
    "demand": "demand",
    "commodity_msha": "commodity",
    "commodity_eia": "commodity",
    # 🔴 A THIRD FAMILY, NEVER MERGED WITH `commodity`.  A well and a metal mine are
    # not two observations of one fact, and the directive keeps any future state
    # (Oklahoma, Texas) separate too unless a specific argued reason says otherwise.
    "oil_gas_nd": "oil_gas",
}
assert set(FAMILY) == set(SOURCES), "a source with no declared family"

# source_system -> the source responsible for it, for attributing a live defect
BY_SYSTEM: dict[str, str] = {
    sys_: sid for sid, s in SOURCES.items() for sys_ in s.source_systems
    # 'scope' is shared by contract and demand; a defect in a scope-written row
    # is not attributable to one of them, so it is deliberately left unmapped
    if len([1 for o in SOURCES.values() if sys_ in o.source_systems]) == 1
}
