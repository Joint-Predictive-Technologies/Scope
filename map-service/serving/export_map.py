#!/usr/bin/env python3
"""Block 11 — the read-only export that replaces the map prototype's synthetic
generator (`buildUniverse` / `statusPick` / `mkSignal`) with the real graph.

    python serving/export_map.py --out serving/out/map-v1

🔴 READ-ONLY, ENFORCED, NOT PROMISED.  The database is opened `mode=ro` through a
URI, so a stray write raises rather than succeeding quietly.  The export
directory is regenerable and is NOT the moat; `osint.db` is never touched.

────────────────────────────────────────────────────────────────────────────────
WHAT THIS EMITS, AND WHY EACH FIELD IS THE SHAPE IT IS

The frontend already consumes a settled shape.  This does not redesign it; it
fills it with facts.  Field-by-field, against `mkSignal()`:

  confidence  the confidence the GRAPH records for the contributing edge —
              `edges.confidence_score`, or `demand_relevance.confidence_score`.
              Never composed across sources into one number: a county holds an
              ARRAY of signals, each carrying its own source's confidence, which
              is what the state layer already did in the prototype.
  events      a real count of the underlying rows.  Never an invented integer.
  tier        `source_reliability_tier`, as recorded.
  ev          `direct_or_inferred`, as recorded.
  seed        a real `entities.entity_id`, resolved through `entity_canonical`
              so the graph plane opens on the canonical company and not on a
              stray that the rest of the system has already rolled up.
  shape       ⚠️ NOT a catalyst pattern.  The prototype's `SHAPES` array is the
              six patterns of `osint-signal-taxonomy.md`, and that document's own
              status says five of the six are untestable against this corpus and
              the sixth failed its base-rate test.  Emitting one would be a
              claim the graph cannot support.  `shape` therefore names WHAT KIND
              OF OBSERVATION this is, which is a thing the rows actually know.
  contra      🔴 ABSENT.  See `--- contradicting evidence ---` below.

────────────────────────────────────────────────────────────────────────────────
--- coverage ---

`no-signal` means "checked here, found nothing".  ⚠️ THIS PARAGRAPH USED TO READ
"No source in this corpus checks a PLACE", which is the exact sentence
`map_sources.py` records the precedent's verifier as having overturned — restated
here a few lines away from the record of its overturning.  The true position:
four of the five sources check an ENTITY that turns out to have a place; MSHA's
registry does enumerate places, and the slice loaded from it still cannot support
`no-signal` for a separate, measured reason.  See `map_sources.py`, where each
source carries its own frame.  So this export emits exactly two states —
`has-signal` and `no-coverage` — and `no-signal` appears nowhere.  That is a
finding about the corpus, not a setting.

--- contradicting evidence ---

🔴 `contra` IS NOT EMITTED, AND NOT AS AN OVERSIGHT.  Every `edges` row but one
carries `contradicting_sources = '[]'`, and the one exception is prose stating
there is none.  ⚠️ THAT COUNT USED TO BE WRITTEN OUT HERE — "249,680 of 249,681" —
and this work order's own 752 new edges made it stale, in the same file that
lectures about hand-authored literals going stale.  It is now measured at export
time by `scan_contra()` and reported in the manifest.  `demand_relevance` is blunter still —
its stored value reads "CHECKED AND NONE REPRESENTABLE: the graph holds 0 rows of
every disqualifying event type ... This is NOT evidence of no disqualification —
it is evidence the graph cannot currently express one."

Rendering "Contradicting: none" on every county would assert a screen that never
happened — the same error the coverage grammar exists to prevent, one field over.
The inspector is given `contra_state: "not-screenable"` plus that sentence, and
the contradiction ring is not drawn at all.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from map_sources import (SOURCES, NO_SOURCE_SWEEPS, BY_SYSTEM, FAMILY, NODE_TYPES,
                         SWEEP_SCOPES)
import routability

# the loader package of this same repo — one definition of the rule, shared
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "loader"))
from text_encoding import repair_mojibake, looks_mangled as _looks_mangled

DEFAULT_DB = os.path.expanduser("~/dev/osint-graph/data/osint.db")

CONTRA_STATEMENT = (
    "Not screenable. The graph holds zero rows of every disqualifying event type "
    "(regulatory_action, lawsuit_filed, investigation_opened, company_dissolved, "
    "divestiture), so an absence of contradiction here is evidence about the graph, "
    "not about the world."
)


# 🔴 IMPORTED, NOT FORKED.  This file used to carry its own copy of the detector,
# and an independent verifier caught that the copy had ALREADY DIVERGED from
# `loader/text_encoding.py`: three unwinding passes instead of four, and no
# falsy-name guard, so `mojibake(None)` raised where `repair_mojibake(None)`
# returns None — harmless only because `display_name` is NOT NULL.  Two
# implementations of one rule is how the map plane and the graph plane end up
# disagreeing about what counts as a defect.
mojibake = repair_mojibake
looks_mangled = _looks_mangled


def scan_defects(con) -> dict:
    """Live encoding defects, measured NOW and attributed to a source.

    🔴 DERIVED, NEVER DECLARED.  `map_sources.py` used to carry a hand-written
    sentence naming one corrupt county; a concurrent session repaired that row and
    the sentence outlived it by an hour.  Whatever this returns is true of the
    database this export just read, and stops being asserted when it stops being
    true.

    🔴 EVERY ENTITY, NOT JUST PLACES.  The first version joined `locations` and so
    scanned only place names — which was exactly wrong the moment the 61 corrupt
    city names were repaired, because the one remaining defect is a COMPANY:
    `University Of PittsburghÃ¢â‚¬â€ …`, patentsview-sourced and mangled past
    reversibility.  Scanning places only would have declared the patent layer
    clean while a visibly corrupt name from that same layer still rendered in the
    graph plane.

    ⚠️ REVERSIBLE AND IRREVERSIBLE ARE COUNTED SEPARATELY.  A name mangled twice
    can stop being repairable; reporting only what can be repaired would understate
    the total, which is the mislabelled-denominator error this project keeps
    making.  Both numbers, always.
    """
    reversible, irreversible = [], []
    by_source: dict[str, int] = {}
    for eid, name, etype, ssys in con.execute(
            "SELECT entity_id, display_name, entity_type, source_system FROM entities"):
        fix = mojibake(name)
        kind = "reversible" if fix else ("irreversible" if looks_mangled(name) else None)
        if kind is None:
            continue
        src = BY_SYSTEM.get(ssys)
        rec = {"entity_id": eid, "stored": name, "should_be": fix,
               "entity_type": etype, "source_system": ssys, "source": src, "kind": kind}
        (reversible if fix else irreversible).append(rec)
        if src:
            by_source[src] = by_source.get(src, 0) + 1
    rows = reversible + irreversible
    return {"total": len(rows), "reversible": len(reversible),
            "irreversible": len(irreversible), "by_source": by_source, "rows": rows,
            # 🔴 SAY WHAT THE NUMBER COUNTS.  A verifier found the one irreversible
            # string ALSO baked into `entities.external_key` and an `edges.edge_id`
            # — three occurrences of one corruption. `total` counts DISPLAY NAMES,
            # which is what this surface renders; it is not "corrupt strings in the
            # graph", and reporting it as though it were would be the mislabelled-
            # denominator error one more time.
            "counts": "entity display_name values only — the same corrupt string may "
                      "also occur in identity columns (external_key, edge_id) that "
                      "this scan does not read and that cannot be rewritten anyway",
            "kind": "entity display names mis-decoded from UTF-8",
            "cause": "PatentsView publishes them this way — confirmed against live "
                     "BigQuery; not a decoding fault in this pipeline",
            "detector_is_a_floor": True}


def scan_contra(con) -> dict:
    """The contradiction census, MEASURED — it used to be a sentence in a docstring.

    🔴 DERIVED FOR THE SAME REASON `scan_defects()` IS.  The literal "249,680 of
    249,681" was true when it was written and false by the end of this work order,
    because the commodity load added 752 edges.  A count that ships to a user must
    expire when it stops being true.
    """
    total, screened = con.execute(
        "SELECT COUNT(*), SUM(contradicting_sources <> '[]') FROM edges").fetchone()
    return {"edges": total, "with_contradicting_sources": screened or 0,
            "counts": "edges rows whose contradicting_sources is not the empty list; "
                      "the sole live exception is prose stating there is none"}


def connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def change_counter(db_path: str) -> int:
    """SQLite's file change counter, bytes 24..27 — the cheap identity stamp that
    an md5 of the file cannot give (see [[sqlite-md5-not-a-content-seal]])."""
    with open(db_path, "rb") as f:
        return int.from_bytes(f.read(28)[24:28], "big")


# ══════════════════════════════════════════════════════════════════════════════
# geography
# ══════════════════════════════════════════════════════════════════════════════

def load_geography(con):
    """county entity_id -> (fips5, name), and the city -> county parent map.

    ⚠️ `patent_location` resolves to CITY, never to county — no row in the table
    claims better than `city`, because PatentsView has nothing better.  A county
    is reached only through `locations.parent_location_id`, and 85.0% of located
    mentions get there.  The other 15.0% are foreign, or are the CT/AK/USVI cases
    whose county tier was deliberately dropped rather than crosswalked.  They are
    NOT counted into any county, and the manifest says how many they are."""
    counties, states = {}, {}
    for r in con.execute("""
        SELECT l.entity_id eid, l.location_type lt, e.display_name nm,
               e.canonical_anchor_value v
          FROM locations l JOIN entities e ON e.entity_id = l.entity_id
         WHERE l.location_type IN ('county','county_equivalent','state')"""):
        if r["lt"] == "state":
            states[r["eid"]] = (r["v"], r["nm"])
        else:
            counties[r["eid"]] = (r["v"], r["nm"], r["lt"])

    city2county = {}
    for eid, parent in con.execute("""
        SELECT entity_id, parent_location_id FROM locations
         WHERE location_type='city' AND parent_location_id IS NOT NULL"""):
        if parent in counties:
            city2county[eid] = parent
    return counties, states, city2county


def canonical_map(con):
    """stray entity_id -> canonical entity_id.  `entity_canonical` forbids chains
    by trigger, so one hop is the whole resolution — asserted, not assumed."""
    m = {r[0]: r[1] for r in con.execute(
        "SELECT entity_id, canonical_entity_id FROM entity_canonical")}
    assert not (set(m) & set(m.values())), "entity_canonical chain — trigger bypassed?"
    return m


# ══════════════════════════════════════════════════════════════════════════════
# the three source layers
# ══════════════════════════════════════════════════════════════════════════════

def contract_signals(con, counties, canon):
    """One signal per (county), aggregating that county's contract awards.

    Place of performance is WHERE THE WORK HAPPENS.  It is not the recipient's
    address and the loader refuses to substitute one for the other; this export
    inherits that and does no fallback of its own."""
    per = defaultdict(lambda: {"awards": [], "conf": [], "entities": defaultdict(int)})
    q = """
        SELECT ev.event_id, ev.location_entity_id loc, ev.subject_entity_id subj,
               ev.event_timestamp ts, ev.raw_payload rp,
               (SELECT MIN(confidence_score) FROM edges e WHERE e.event_id = ev.event_id
                  AND e.relationship_type='government_contract') conf,
               (SELECT MIN(source_reliability_tier) FROM edges e WHERE e.event_id = ev.event_id
                  AND e.relationship_type='government_contract') tier,
               en.display_name subj_name
          FROM events ev JOIN entities en ON en.entity_id = ev.subject_entity_id
         WHERE ev.event_type='government_contract_awarded'
           AND ev.location_entity_id IS NOT NULL"""
    for r in con.execute(q):
        if r["loc"] not in counties:
            continue
        fips = counties[r["loc"]][0]
        d = per[fips]
        payload = json.loads(r["rp"]) if r["rp"] else {}
        seed = canon.get(r["subj"], r["subj"])
        d["entities"][seed] += 1
        d["awards"].append({
            "entity_id": seed,
            "raw_entity_id": r["subj"],
            "name": r["subj_name"],
            "date": r["ts"],
            "award_id": payload.get("award_id"),
            "amount": payload.get("amount"),
            "description": payload.get("description"),
        })
        if r["conf"] is not None:
            d["conf"].append((r["conf"], r["tier"]))
    out = {}
    for fips, d in per.items():
        # 🔴 MIN, never mean.  A county's contract evidence is only as good as its
        # weakest contributing edge; averaging would let one 0.95 award launder a
        # 0.6 one whose award id never resolved.
        conf = min(c for c, _ in d["conf"]) if d["conf"] else None
        tier = max(t for _, t in d["conf"]) if d["conf"] else SOURCES["contract"].tier
        if conf is None:
            continue
        top = max(d["entities"].items(), key=lambda kv: (kv[1], kv[0]))[0]
        d["awards"].sort(key=lambda a: (a["amount"] or 0), reverse=True)
        out[fips] = {
            # 🔴 THE FULL AWARDEE SET, SEPARATE FROM THE DISPLAY LIST.  `detail` is
            # truncated to 12 for the panel, and the demand layer used to read its
            # county-touch map out of that truncated list — so a county with more
            # than 12 awardees would have silently dropped demand attributions with
            # nothing saying so.  Today the maximum is 6 awardees in one county, so
            # it never bit; a latent defect that only waits for more data is still a
            # defect.
            "awardees": sorted(d["entities"]),
            "source": "contract",
            "shape": "Contract place of performance",
            "confidence": round(conf, 3),
            "events": len(d["awards"]),
            "tier": tier,
            "ev": SOURCES["contract"].evidence,
            "verification": SOURCES["contract"].verification,
            "seed": top,
            "detail": d["awards"][:12],
            # 🔴 THE REMAINDER SHIPS TOO, SO THE PANEL CAN PAGE THROUGH IT WITHOUT A
            # GRAPH REQUEST.  `detail` stays the 12-row cap the panel opens on;
            # `detail_rest` is everything after it in the same order and shape.
            # Additive: no reader of `detail`/`detail_total` changes meaning.
            "detail_rest": d["awards"][12:],
            "detail_total": len(d["awards"]),
        }
    return out


def patent_signals(con, counties, city2county, canon):
    """One signal per county, over patent mentions whose city has a county parent.

    ⚠️ PENDING VERIFICATION.  Marked `"verification": "pending"` on every row it
    emits, because the geocoding session that produced `patent_location` says of
    itself: 'NOT SETTLED ... no independent verifier pass has run.'"""
    mentions = defaultdict(int)
    by_role = defaultdict(lambda: defaultdict(int))
    patents = defaultdict(set)
    holders = defaultdict(lambda: defaultdict(set))
    # 🔴 assignee-only, kept SEPARATE and never merged into `holders`.  An
    # inventor's address is where a PERSON lives; an assignee's is where the
    # COMPANY is.  Anything that wants to say "this company is here" — the demand
    # layer does — must use the second and not the first.
    seats = defaultdict(lambda: defaultdict(int))
    dropped_county_tier = 0

    owner = {}
    for pid, oid in con.execute("""
        SELECT entity_b_id, entity_a_id FROM edges
         WHERE relationship_type='patent_ownership'"""):
        owner.setdefault(pid, canon.get(oid, oid))

    for pid, loc, role, county_dropped in con.execute("""
        SELECT patent_entity_id, location_entity_id, role, county_tier_dropped_fips
          FROM patent_location"""):
        if loc is None:
            continue
        co = city2county.get(loc)
        if co is None:
            if county_dropped:
                dropped_county_tier += 1
            continue
        fips = counties[co][0]
        mentions[fips] += 1
        by_role[fips][role] += 1
        patents[fips].add(pid)
        o = owner.get(pid)
        if o:
            holders[fips][o].add(pid)
            if role == 'assignee':
                seats[fips][o] += 1

    names = {}
    want = {e for h in holders.values() for e in h}
    want |= {e for h in seats.values() for e in h}
    if want:
        qmarks = ",".join("?" * len(want))
        names = {r[0]: r[1] for r in con.execute(
            f"SELECT entity_id, display_name FROM entities WHERE entity_id IN ({qmarks})",
            tuple(want))}

    src = SOURCES["patent"]
    out = {}
    for fips, n_mentions in mentions.items():
        hs = sorted(holders[fips].items(), key=lambda kv: (-len(kv[1]), names.get(kv[0], "")))
        out[fips] = {
            "source": "patent",
            "shape": "Patent address concentration",
            # every patent_ownership edge in the graph is 0.9, verbatim — not a
            # constant this file chose
            "confidence": 0.9,
            "events": len(patents[fips]),
            "mentions": n_mentions,
            "tier": src.tier,
            "ev": src.evidence,
            "seed": hs[0][0] if hs else None,
            "verification": src.verification,
            "roles": dict(by_role[fips]),
            "detail": [{"entity_id": e, "name": names.get(e, e), "patents": len(p)}
                       for e, p in hs[:12]],
            "detail_rest": [{"entity_id": e, "name": names.get(e, e), "patents": len(p)}
                            for e, p in hs[12:]],
            "detail_total": len(hs),
        }
    return out, dropped_county_tier, {f: dict(v) for f, v in seats.items()}


def demand_signals(con, counties, city2county, canon, contract_locs, patent_seats):
    """Demand relevance reaches the map ONLY through an entity's located facts.

    🔴 IT DOES NOT USE `demand_signal.geography_tags`.  Those four county codes are
    real rows, but the traversal loader's current code names two different Texas
    counties and seeds with `INSERT OR IGNORE`, so the stored tags can never be
    corrected by a re-run — the persisted signal and its persisted geography rows
    are internally consistent and jointly superseded by the code.  Putting them on
    a map would render a superseded definition of the demand.

    Anchoring instead on where the named company's OWN contracts and ASSIGNEE
    addresses actually are keeps every county attribution resting on a first-party
    fact (USAspending place of performance, PatentsView assignee address) rather
    than on a hand-authored tag list.

    🔴 ASSIGNEE ADDRESSES ONLY, AND THE FIRST CUT GOT THIS WRONG.  Attributing a
    company's demand relevance to every county an INVENTOR lives in put demand on
    1,148 of 1,386 counties — Apple's quantum-adjacent CPC match reaching every
    county an Apple inventor happens to live in.  An inventor's home is not a
    company's location, and treating it as one turned a discriminating signal into
    a near-uniform wash.  Restricted to assignee seats + contract place of
    performance, which is what "this company operates here" actually means."""
    # 🔴 `match_type='geography'` IS EXCLUDED, and the exclusion is the point.
    # Those four rows' `match_evidence` ("located in Sagadahoc, Maine") is derived
    # from `demand_signal.geography_tags`, which the traversal loader's current
    # code has superseded and `INSERT OR IGNORE` can never correct.  Block 8's
    # ranking report excluded them for that reason and pinned the exclusion with a
    # test; this surface inherits the decision rather than quietly reversing it.
    # Nothing is lost: Sagadahoc still carries the icebreaker demand, through
    # GENERAL DYNAMICS CORP's *category* match and Bath Iron Works' real assignee
    # address, both of which are first-party facts.
    rows = list(con.execute("""
        SELECT dr.demand_id, dr.entity_id, dr.match_type, dr.match_evidence,
               dr.confidence_score conf, dr.source_reliability_tier tier,
               dr.direct_or_inferred ev, dr.contradicting_sources contra,
               ds.description descr, en.display_name nm
          FROM demand_relevance dr
          JOIN demand_signal ds ON ds.demand_id = dr.demand_id
          JOIN entities en ON en.entity_id = dr.entity_id
         WHERE dr.match_type <> 'geography'"""))

    # canonical entity -> its demand matches (a stray's match belongs to its parent)
    by_canon = defaultdict(list)
    for r in rows:
        by_canon[canon.get(r["entity_id"], r["entity_id"])].append(r)

    # which counties does a canonical entity actually touch?
    touches = defaultdict(set)
    for fips, sig in contract_locs.items():
        for ent in sig["awardees"]:          # NOT sig["detail"] — see `awardees`
            touches[ent].add(fips)
    for fips, seat_owners in patent_seats.items():
        for ent in seat_owners:
            touches[ent].add(fips)

    per = defaultdict(list)
    for ent, matches in by_canon.items():
        for fips in touches.get(ent, ()):
            per[fips].extend((ent, m) for m in matches)

    out = {}
    for fips, pairs in per.items():
        best = min(p[1]["conf"] for p in pairs)
        detail = []
        seen = set()
        for ent, m in sorted(pairs, key=lambda p: (-p[1]["conf"], p[1]["nm"])):
            k = (ent, m["demand_id"], m["match_type"])
            if k in seen:
                continue
            seen.add(k)
            detail.append({
                "entity_id": ent, "name": m["nm"], "demand_id": m["demand_id"],
                "demand": m["descr"], "match_type": m["match_type"],
                "evidence": m["match_evidence"], "confidence": m["conf"],
            })
        top = detail[0]["entity_id"]
        out[fips] = {
            "source": "demand",
            "shape": "Demand-relevant holder located here",
            "confidence": round(best, 3),
            "events": len(detail),
            "tier": max(p[1]["tier"] for p in pairs),
            "ev": "inferred",
            "verification": SOURCES["demand"].verification,
            "seed": top,
            "detail": detail[:12],
            "detail_rest": detail[12:],
            "detail_total": len(detail),
        }
    return out


# ══════════════════════════════════════════════════════════════════════════════
# the commodity layers — MSHA and EIA-851A, never merged
# ══════════════════════════════════════════════════════════════════════════════

def commodity_signals(con, counties, states, canon):
    """Live mineral sites, as TWO signal layers that are never combined.

    🔴 MSHA AND EIA GET SEPARATE SIGNALS IN THE SAME COUNTY ARRAY.  Their loaders
    carry one shared, verifier-upheld invariant — the two sources are never merged,
    because they genuinely contradict each other about the same physical sites (5
    of the 6 facilities EIA calls Operating at end-2025 are Abandoned in MSHA).
    Collapsing them into one `commodity` signal would take a MIN across two sources
    that are ALLOWED to disagree, which is the laundering the never-merge rule
    exists to prevent.  A county holding both gets two entries, exactly as the
    cardinality rule already requires for the other three layers.

    ── precision, resolved rather than defaulted ──────────────────────────────
    Measured on the live graph, not assumed:

        MSHA  313 `coordinate`  + 1 `county`   = 314   -> all 314 reach a county
        EIA    21 `county`      + 3 `state`    =  24   ->  21 reach a county

    Every one of the 314 MSHA sites carries a county `location_entity_id` — the
    coordinate is a FINER fact recorded alongside it, never the only one — so the
    county plane loses nothing at either MSHA tier.  `precision` is carried through
    to the panel so a coordinate-tier site is not presented as if the map had
    derived its position from a county, nor the reverse.

    🔴 THE 3 STATE-TIER EIA FACILITIES ARE NOT PUT IN A COUNTY, AND NOT DROPPED
    EITHER.  Dewey Burdock (Fall River AND Custer, SD), Nichols Ranch and Willow
    Creek (Johnson AND Campbell, WY) each span two counties, and the loader
    deliberately recorded them at `state` precision rather than pick one — "picking
    one would be an invention and averaging them would be a fallback centroid".
    Both county names survive in `raw_payload`, and it is tempting to read them back
    out and light both counties.  This export does NOT, for the same reason it does
    not read `demand_signal.geography_tags`: the persisted precision IS the graph's
    assertion, and an export that upgrades a tier the loader refused is a surface
    quietly reversing a decision one layer up.

    They are emitted instead as STATE-TIER signals on the national plane, at the
    precision they actually have, and named in the manifest with the counties EIA
    published.  Present-but-unmappable-at-county-precision is a fact this map can
    show; a centroid would be one it invented.
    """
    sites = {}
    for r in con.execute("""
        SELECT ev.event_id, ev.subject_entity_id sid, ev.location_entity_id loc,
               ev.geospatial_precision prec, ev.latitude lat, ev.longitude lng,
               ev.event_timestamp ts, ev.raw_payload rp,
               en.display_name nm, en.source_system ssys, en.notes notes,
               en.canonical_anchor_type anchor_t, en.canonical_anchor_value anchor_v
          FROM events ev JOIN entities en ON en.entity_id = ev.subject_entity_id
         WHERE ev.event_type = 'mineral_resource_identified'
           AND ev.location_entity_id IS NOT NULL"""):
        # ⚠️ ONE EVENT PER SITE TODAY (338 events, 338 distinct subjects) AND THE
        # ASSIGNMENT SAYS SO RATHER THAN ASSUMING IT.  A second
        # `mineral_resource_identified` row for one site — a `mineral_resource_
        # updated` follow-up re-typed, say — would have silently replaced the first
        # and the count would still have looked right.
        if r["sid"] in sites:
            raise AssertionError(
                f"two located mineral events for one site ({r['sid']}); this export "
                "supersedes rather than merges and has no rule for choosing")
        sites[r["sid"]] = {
            "entity_id": r["sid"], "source": BY_SYSTEM.get(r["ssys"]),
            "source_system": r["ssys"], "loc": r["loc"], "precision": r["prec"],
            "lat": r["lat"], "lng": r["lng"], "name": r["nm"], "notes": r["notes"],
            "date": r["ts"],
            "anchor": f'{r["anchor_t"]}:{r["anchor_v"]}' if r["anchor_t"] else None,
            "payload": json.loads(r["rp"]) if r["rp"] else {},
            "commodities": [], "holders": [], "conf": [], "tiers": [],
        }

    # ── what each site is worked for ──────────────────────────────────────────
    for r in con.execute("""
        SELECT s.site_entity_id sid, s.commodity_id cid, s.commodity_role role,
               s.confidence_score conf, s.source_reliability_tier tier,
               c.display_name cname
          FROM site_commodity_assertion s
          JOIN commodity c ON c.commodity_id = s.commodity_id"""):
        st = sites.get(r["sid"])
        if st is None:
            continue
        st["commodities"].append({"commodity_id": r["cid"], "name": r["cname"],
                                  "role": r["role"], "confidence": r["conf"]})
        st["conf"].append(r["conf"])
        st["tiers"].append(r["tier"])

    # ── who holds them ────────────────────────────────────────────────────────
    # 🔴 `entity_a_id` ON THE SITE SIDE IS THE DISAMBIGUATION, NOT A CONVENIENCE.
    # `ownership` is directionally ambiguous in this graph and the corrective pass
    # recorded it as a known hazard: `entity_a` is the OWNED thing for the msha/eia
    # families and the OWNER for the corrective parent->subsidiary family.  Keying
    # on the site side is what keeps a company->company parent edge out of a mine's
    # holder list.
    for r in con.execute("""
        SELECT e.entity_a_id sid, e.entity_b_id hid, e.relationship_type rel,
               h.display_name hname, h.resolution_status rs,
               h.canonical_anchor_type h_anchor
          FROM edges e JOIN entities h ON h.entity_id = e.entity_b_id
         WHERE e.relationship_type IN ('ownership', 'contractor')"""):
        st = sites.get(r["sid"])
        if st is None:
            continue
        st["holders"].append({
            "entity_id": canon.get(r["hid"], r["hid"]), "raw_entity_id": r["hid"],
            "name": r["hname"],
            # 🔴 THE EDGE'S OWN relationship_type, NOT A ROLE NAME INVENTED HERE.
            # `ownership`->controller / `contractor`->operator is ALMOST true and
            # measurably not: joined against `holder_period`, 5 `contractor` edges
            # carry holder_role='controller' and 12 `ownership` edges carry
            # 'operator', because one company can be both for one site.  Restating
            # the mapping as a fact would have been a 17-row invention.
            "rel": r["rel"],
            "resolution": r["rs"],
            "anchored": r["h_anchor"] is not None,
        })

    # ── county and state buckets ──────────────────────────────────────────────
    per_county = defaultdict(lambda: defaultdict(list))
    per_state = defaultdict(lambda: defaultdict(list))
    unmappable, unregistered = [], []
    for st in sites.values():
        if st["source"] is None:
            # a site whose source_system this registry does not own is NEVER folded
            # into one of the two layers to keep a total tidy; it is counted here
            # and reported, which is how the next source announces itself
            unregistered.append({"entity_id": st["entity_id"], "name": st["name"],
                                 "source_system": st["source_system"]})
            continue
        if st["loc"] in counties:
            per_county[counties[st["loc"]][0]][st["source"]].append(st)
        elif st["loc"] in states:
            per_state[states[st["loc"]][0]][st["source"]].append(st)
            unmappable.append({
                "entity_id": st["entity_id"], "name": st["name"],
                "source": st["source"], "precision": st["precision"],
                "state": states[st["loc"]][1],
                # the counties the SOURCE published, carried verbatim — this is the
                # evidence that the facility is unmappable at county tier, not a
                # suggestion that it be mapped there
                "source_counties": st["payload"].get("counties"),
                "source_county_geoids": st["payload"].get("county_geoids"),
                "why": "the source publishes a multi-county footprint and no coordinates; "
                       "the loader recorded state precision rather than pick one county, "
                       "and this export does not reverse that",
            })
        else:
            unmappable.append({
                "entity_id": st["entity_id"], "name": st["name"],
                "source": st["source"], "precision": st["precision"],
                "why": "its location entity is neither a county nor a state in this graph",
            })

    def _site_row(st):
        return {
            "entity_id": st["entity_id"], "name": st["name"],
            "anchor": st["anchor"], "status": st["notes"],
            "precision": st["precision"], "lat": st["lat"], "lng": st["lng"],
            "date": st["date"], "commodities": st["commodities"],
            "holders": sorted(st["holders"], key=lambda h: (h["name"], h["rel"])),
        }

    def _sig(source, group):
        # 🔴 MIN, never mean — the same rule the contract layer already holds.  A
        # county's commodity evidence is only as good as its weakest site
        # assertion; averaging would let a 1.0 assertion launder a 0.5 one.
        # ⚠️ LATENT ON TODAY'S DATA, AND SAYING SO IS THE POINT: all 394 live
        # site_commodity_assertion rows carry confidence 1.0 and tier 1, so MIN,
        # MAX and mean coincide on every county in this export.  A rule that only
        # bites on future data is still a rule; it is pinned by a fixture whose
        # assertions deliberately disagree with each other.
        confs = [c for st in group for c in st["conf"]]
        tiers = [t for st in group for t in st["tiers"]]
        commodity_n, prec_n = defaultdict(int), defaultdict(int)
        for st in group:
            prec_n[st["precision"]] += 1
            for c in st["commodities"]:
                commodity_n[c["commodity_id"]] += 1
        src = SOURCES[source]
        group = sorted(group, key=lambda s: (-len(s["commodities"]), s["name"], s["entity_id"]))
        holders = sorted({(h["entity_id"], h["name"]) for st in group for h in st["holders"]})
        return {
            "source": source,
            "shape": src.label,
            "confidence": round(min(confs), 3) if confs else None,
            "events": len(group),
            "tier": max(tiers) if tiers else src.tier,
            "ev": src.evidence,
            "verification": src.verification,
            # 🔴 THE SEED IS THE SITE, NOT ITS HOLDER.  Every one of the 338 sites
            # is a real entity; only 66 have a CIK-resolved holder, and seeding on
            # an unresolved MSHA holder string would open the graph plane on a name
            # with nothing behind it.  The site is also what the dot MEANS.
            "seed": group[0]["entity_id"],
            "seed_kind": "asset",
            "commodities": dict(sorted(commodity_n.items())),
            # 🔴 THE NAMES TOO.  `commodity.display_name` exists precisely so a
            # surface does not render `misc_metal` and `sand_gravel` at a reader;
            # the counts stay keyed on the id, which is the identity.
            "commodity_names": {c["commodity_id"]: c["name"]
                                for st in group for c in st["commodities"]},
            "precision": dict(sorted(prec_n.items())),
            # 🔴 THE FULL HOLDER SET, SEPARATE FROM THE 12-ROW DISPLAY LIST — the
            # same shape as the contract layer's `awardees`, and for the same
            # reason: anything downstream that wants "which companies are here"
            # must not read it out of a truncated panel list.
            "holders": [h[0] for h in holders],
            "detail": [_site_row(st) for st in group[:12]],
            "detail_rest": [_site_row(st) for st in group[12:]],
            "detail_total": len(group),
        }

    counties_out = {f: {s: _sig(s, g) for s, g in by.items()} for f, by in per_county.items()}
    states_out = {ss: {s: _sig(s, g) for s, g in by.items()} for ss, by in per_state.items()}
    stats = {
        "sites": len(sites),
        "sites_by_source": dict(Counter(st["source"] or "UNREGISTERED"
                                        for st in sites.values()).most_common()),
        "sites_by_precision": dict(Counter(st["precision"] for st in sites.values()).most_common()),
        "counties": len(counties_out),
        "counties_msha": sum(1 for v in counties_out.values() if "commodity_msha" in v),
        "counties_eia": sum(1 for v in counties_out.values() if "commodity_eia" in v),
        "counties_both_commodity_sources": sum(1 for v in counties_out.values() if len(v) > 1),
        # the number of FACILITIES, not the number of signal dicts — the first
        # version of this line read `len(g)` over the signal object and reported
        # 28 for 3 facilities, which is a count of dictionary keys
        "state_tier_facilities": sum(g["events"] for v in states_out.values()
                                     for g in v.values()),
        "unmappable_at_county_precision": len(unmappable),
        "sites_with_a_holder_edge": sum(1 for st in sites.values() if st["holders"]),
        "sites_with_a_cik_resolved_holder": sum(
            1 for st in sites.values()
            if any(h["resolution"] == "resolved" for h in st["holders"])),
        "sites_with_no_registered_source": len(unregistered),
    }
    return counties_out, states_out, unmappable, unregistered, stats


# 🔴 NORTH DAKOTA SHIPS 100x MORE ROWS THAN THE COMMODITY LAYER, AND THE LEDGER'S
# "ship every remaining row in `detail_rest`" PATTERN DOES NOT SURVIVE THAT.
# Measured before choosing: one well row in the ledger's own shape is ~480 bytes,
# so McKenzie County's 9,205 wells alone would be 4.4 MB in a single county file
# and all of North Dakota 17.7 MB — against California's existing 1.7 MB, already
# the file this campaign flagged as too heavy.  So oil/gas ships a BOUNDED rest
# and the page states the remainder: `ledgerModel()` already computes
# `unshipped = detail_total - (detail + detail_rest)` and renders it through
# `hiddenNote()`, so a bounded rest is disclosed, not hidden.  `detail_total`
# stays the TRUE well count.
# ⚠️ THIS CAP IS NOT APPLIED TO ANY OTHER LAYER.  Contract, patent, demand and
# commodity keep shipping their whole remainder, because their remainders are
# small enough to; a global cap would have silently truncated them too.
OILGAS_REST_CAP = 48


def site_points(con) -> dict:
    """Every commodity/oil-gas site that carries a REAL coordinate, for the canvas.

    🔴 A SEPARATE PAYLOAD, AND SEPARATE ON PURPOSE.  The county ledger answers
    "what is here"; the canvas layer answers "where is it", and 37,230 points
    cannot ride inside the county files — measured, the ledger's own row shape
    would put 4.4 MB in McKenzie alone.  This file is fetched ONLY when the
    commodity layer is switched on, so a reader who never opens it pays nothing.

    🔴 COLUMNAR INTEGERS, NOT OBJECTS.  `{"lat":..,"lng":..}` per point is ~83
    bytes and 3.09 MB total; four parallel integer arrays are ~0.8 MB for the
    same 37,230 points, which is the difference between a toggle that feels
    instant and one that stalls.

    ⚠️ COORDINATES ARE ROUNDED TO 4 DECIMAL PLACES (~11 m) AND THAT IS A CLAIM.
    It is stated in the payload rather than left for a reader to discover: these
    are map dots, not survey positions, and 11 m is far finer than the 3-7 px a
    dot occupies at any zoom this map offers.  The ledger rows keep the full
    precision the graph holds — nothing is lost, only this rendering payload is
    coarsened.

    🔴 ONLY `coordinate` PRECISION IS EMITTED.  A county-tier or state-tier site
    has no point and is NOT given a centroid — the same refusal the commodity
    layer already makes.  6,954 ND wells carry no coordinate at all and are
    absent here by the same rule; the manifest counts them.
    """
    rows = con.execute("""
        SELECT e.source_system AS ssys, ev.latitude AS lat, ev.longitude AS lng,
               e.entity_id AS eid
          FROM events ev
          JOIN entities e ON e.entity_id = ev.subject_entity_id
         WHERE ev.latitude IS NOT NULL AND ev.longitude IS NOT NULL
           AND ev.geospatial_precision = 'coordinate'
           AND e.source_system IN ('ndic','msha','eia')
      ORDER BY e.source_system, e.entity_id
    """).fetchall()

    com_by_site = defaultdict(set)
    for r in con.execute("""
        SELECT a.site_entity_id AS sid, a.commodity_id AS cid
          FROM site_commodity_assertion a
          JOIN entities e ON e.entity_id = a.site_entity_id
         WHERE e.source_system IN ('ndic','msha','eia')
    """):
        com_by_site[r["sid"]].add(r["cid"])

    # the commodity index is a CENSUS of what these sites actually assert, in a
    # stable order — never a hand-authored list, the same rule `node_types` holds
    com_index = sorted({c for cs in com_by_site.values() for c in cs})
    com_bit = {c: i for i, c in enumerate(com_index)}
    names = {r[0]: r[1] for r in con.execute(
        "SELECT commodity_id, display_name FROM commodity")}

    fam_index = ["commodity", "oil_gas"]
    fam_of = {"msha": 0, "eia": 0, "ndic": 1}

    lat, lng, fam, com = [], [], [], []
    for r in rows:
        lat.append(round(r["lat"] * 10000))
        lng.append(round(r["lng"] * 10000))
        fam.append(fam_of[r["ssys"]])
        mask = 0
        for c in com_by_site.get(r["eid"], ()):
            mask |= 1 << com_bit[c]
        com.append(mask)
    return {
        "schema": "columnar-int4",
        "note": "lat/lng are degrees x 10000 (4 dp, ~11 m); `com` is a bitmask "
                "over `commodities`; `fam` indexes `families`.",
        "count": len(lat),
        "families": fam_index,
        "commodities": com_index,
        "commodity_names": {c: names.get(c, c) for c in com_index},
        "lat": lat, "lng": lng, "fam": fam, "com": com,
    }


def swept_county_universe(state_fips: str) -> dict[str, str]:
    """Every county FIPS in a state, from Census 2020 — the county universe a
    `no-signal` claim needs and `entities` cannot supply.

    🔴 THE UNIVERSE MUST NOT COME FROM THE SOURCE BEING SWEPT.  NDIC's registry
    names 52 counties; Traill is absent from it BECAUSE it holds no wells, so
    asking the registry for the denominator would make the one county that
    matters invisible and the sweep unfalsifiable.  Census 2020 is independent of
    NDIC and is the same authority the ND loader validated its 52 derivations
    against (52/52 by name, a mismatch REFUSING the load).
    ⚠️ AND IT MUST NOT COME FROM `entities` EITHER — that is the map's own
    county-universe gap, 1,469 of ~3,144, and it does not hold Traill at all.
    Vendored beside this file so the export stays deterministic and offline.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "national_county2020.txt")
    out = {}
    with open(path, encoding="latin-1") as f:
        next(f)
        for line in f:
            parts = line.rstrip("\n").split("|")
            if len(parts) > 4 and parts[1] == state_fips:
                out[parts[1] + parts[2]] = parts[4]
    if not out:
        raise SystemExit(f"no Census counties for state {state_fips!r}; a sweep "
                         f"scope with no universe cannot emit `no-signal`")
    return out


def oilgas_signals(con, counties, canon):
    """North Dakota oil and gas wells, as ONE signal per county.

    🔴 A THIRD FAMILY, NEVER MERGED WITH `commodity`.  A well is not a metal mine
    seen from another angle; MSHA/EIA are one family because they describe the
    SAME physical sites under two anchors, and nothing about NDIC's registry
    overlaps either.  `FAMILY` says so explicitly rather than by parsing the id.

    ── precision, read from the graph rather than assumed ────────────────────
    ND publishes NO coordinate-precision field at all (the load session records
    `raw_payload.location_precision_source` as explicitly null), so precision
    here is the EVENT's `geospatial_precision`, which the loader set from the
    presence of a real lat/lng.  Measured on the live graph: 36,917 `coordinate`
    and nothing else.  There is no county-tier or state-tier well, so the
    state-tier-facility pattern the EIA layer needed is not exercised — and that
    is stated rather than silently unimplemented.

    🔴 6,954 WELLS ARE LOADED AND NOT LOCATED, AND THEY ARE NOT IN THIS LAYER.
    They carry no `events` row because ND publishes exactly one date and they
    have none, so the graph holds no coordinate and no county for them.  They are
    counted and returned, never quietly dropped, and never given a centroid.
    """
    src = SOURCES["oil_gas_nd"]

    # one pass over the located wells: county, coordinate, status, operator
    rows = con.execute("""
        SELECT w.entity_id AS wid, w.display_name AS wname, w.entity_type AS wtype,
               w.canonical_anchor_value AS api,
               ev.latitude AS lat, ev.longitude AS lng,
               ev.geospatial_precision AS prec,
               substr(ev.event_timestamp,1,10) AS spud,
               loc.canonical_anchor_value AS fips
          FROM entities w
          JOIN events ev ON ev.subject_entity_id = w.entity_id
                        AND ev.event_type = 'drilling_activity'
          JOIN entities loc ON loc.entity_id = ev.location_entity_id
         WHERE w.source_system = 'ndic'
      ORDER BY loc.canonical_anchor_value, w.display_name, w.entity_id
    """).fetchall()

    # commodity assertions, MIN confidence and worst tier per well
    assertions = defaultdict(list)
    for r in con.execute("""
        SELECT a.site_entity_id AS sid, a.commodity_id AS cid, c.display_name AS cname,
               a.commodity_role AS role, a.confidence_score AS conf,
               a.source_reliability_tier AS tier, a.direct_or_inferred AS di
          FROM site_commodity_assertion a
          JOIN entities e ON e.entity_id = a.site_entity_id
          JOIN commodity c ON c.commodity_id = a.commodity_id
         WHERE e.source_system = 'ndic'
    """):
        assertions[r["sid"]].append(dict(r))

    # the operator edge: well -> company, `contractor`, entity_a is the well
    operators = {}
    for r in con.execute("""
        SELECT ed.entity_a_id AS wid, eb.entity_id AS oid, eb.display_name AS oname,
               eb.resolution_status AS res, ed.relationship_type AS rel
          FROM edges ed
          JOIN entities ea ON ea.entity_id = ed.entity_a_id
          JOIN entities eb ON eb.entity_id = ed.entity_b_id
         WHERE ea.source_system = 'ndic'
    """):
        operators[r["wid"]] = dict(r)

    per_county = defaultdict(list)
    for r in rows:
        fips = r["fips"]
        if not fips or len(fips) != 5:
            continue
        per_county[fips].append(dict(r))

    def _well_row(w):
        a = assertions.get(w["wid"], [])
        op = operators.get(w["wid"])
        row = {
            "entity_id": w["wid"], "name": w["wname"],
            "anchor": w["api"], "status": w["wtype"],
            "precision": w["prec"], "lat": w["lat"], "lng": w["lng"],
            "date": w["spud"],
            "commodities": [{"commodity_id": x["cid"], "name": x["cname"],
                             "role": x["role"], "confidence": x["conf"]} for x in a],
            # the operator rides in `holders` so the panel's existing
            # mineral-site row shape renders it with no new branch
            "holders": ([{"entity_id": op["oid"], "name": op["oname"],
                          "rel": op["rel"], "resolution": op["res"]}] if op else []),
        }
        return row

    def _rest_with_min(wells, assertions):
        """The bounded remainder — plus, always, the well that DECIDES the county's
        confidence.

        🔴 A PANEL MUST BE ABLE TO JUSTIFY ITS OWN NUMBER.  Measured on this
        export: 2 of 52 counties (38075 Renville, 38089 Stark) take their MIN from
        a permit-backed 0.5 assertion, and under a straight positional cap the
        deciding well fell outside the 60 shipped rows — so the county read
        "conf 0.50" while every row a reader could open read 1.00.  The aggregate
        was disclosed and the reason was not.  The lowest-confidence well is
        therefore always shipped, even when the cap would have cut it.
        """
        rest = list(wells[12:12 + OILGAS_REST_CAP])
        shipped = {w["wid"] for w in wells[:12]} | {w["wid"] for w in rest}
        def _minconf(w):
            cs = [x["conf"] for x in assertions.get(w["wid"], []) if x["conf"] is not None]
            return min(cs) if cs else None
        scored = [(c, w) for w in wells for c in (_minconf(w),) if c is not None]
        if scored:
            lo = min(scored, key=lambda t: (t[0], t[1]["wid"]))[1]
            if lo["wid"] not in shipped:
                rest.append(lo)
        return rest

    counties_out, stats_prec, stats_status = {}, defaultdict(int), defaultdict(int)
    for fips, wells in per_county.items():
        confs = [x["conf"] for w in wells for x in assertions.get(w["wid"], [])
                 if x["conf"] is not None]
        tiers = [x["tier"] for w in wells for x in assertions.get(w["wid"], [])
                 if x["tier"] is not None]
        commodity_n, commodity_names, prec_n = defaultdict(int), {}, defaultdict(int)
        for w in wells:
            prec_n[w["prec"]] += 1
            stats_prec[w["prec"]] += 1
            stats_status[w["wtype"]] += 1
            for x in assertions.get(w["wid"], []):
                commodity_n[x["cid"]] += 1
                commodity_names[x["cid"]] = x["cname"]
        holders = sorted({(operators[w["wid"]]["oid"], operators[w["wid"]]["oname"])
                          for w in wells if w["wid"] in operators})
        counties_out[fips] = {
            "source": "oil_gas_nd",
            "shape": src.label,
            # 🔴 MIN, never mean — the same rule the commodity layer holds, and
            # unlike that layer it is NOT latent here.  ND's assertions genuinely
            # disagree (1.0 `direct` on a drilled well, 0.5 `inferred` on a permit
            # where the loader itself says no hole was made).
            # ⚠️ MEASURED, NOT ASSERTED: it bites on 2 of the 52 counties —
            # 38075 Renville and 38089 Stark — and nowhere else.  "The rule is
            # exercised" is a claim worth exactly the two counties that exercise
            # it, and the commodity layer's own note about a latent rule is why
            # this one is counted rather than described.
            "confidence": round(min(confs), 3) if confs else None,
            "events": len(wells),
            "tier": max(tiers) if tiers else src.tier,
            "ev": src.evidence,
            "verification": src.verification,
            "seed": wells[0]["wid"],
            "commodities": dict(sorted(commodity_n.items())),
            "commodity_names": commodity_names,
            "precision": dict(sorted(prec_n.items())),
            "holders": [h[0] for h in holders],
            "detail": [_well_row(w) for w in wells[:12]],
            "detail_rest": [_well_row(w) for w in _rest_with_min(wells, assertions)],
            # the TRUE count; the page derives `unshipped` from it and says so
            "detail_total": len(wells),
        }

    # wells the graph holds and cannot place — counted, never dropped.
    # 🔴 DERIVED, NOT RE-QUERIED.  The obvious `NOT EXISTS (SELECT 1 FROM events
    # ...)` is a CORRELATED SUBQUERY and `events` carries no index on
    # `subject_entity_id`, so it degrades to a full scan of 225,803 events for
    # each of 43,871 wells — measured: it did not finish in 2 minutes and hung
    # the whole export.  Total minus the located set is the same number, exact,
    # and costs one already-planned COUNT.
    total_wells = con.execute(
        "SELECT COUNT(*) FROM entities WHERE source_system='ndic' "
        "AND entity_type IN ('asset','permit')").fetchone()[0]
    located_ids = {r["wid"] for r in rows}
    unlocated = total_wells - len(located_ids)

    stats = {
        "wells_total": total_wells,
        "wells_located": len(located_ids),
        "wells_unlocated": unlocated,
        "counties": len(counties_out),
        "wells_by_precision": dict(sorted(stats_prec.items())),
        "wells_by_entity_type": dict(sorted(stats_status.items())),
        "wells_with_an_operator_edge": sum(1 for r in rows if r["wid"] in operators),
        "rest_cap": OILGAS_REST_CAP,
        "rows_shipped": sum(len(v["detail"]) + len(v["detail_rest"])
                            for v in counties_out.values()),
        "rows_unshipped": sum(v["detail_total"] - len(v["detail"]) - len(v["detail_rest"])
                              for v in counties_out.values()),
    }
    return counties_out, stats


def scan_commodity_defects(con) -> dict:
    """Live defects in the commodity layers, measured NOW and attributed.

    🔴 A SEPARATE SCAN, NOT A WIDENED `scan_defects()`, AND THAT IS DELIBERATE.
    `scan_defects()` states exactly what its number counts — "entity display_name
    values only" — after a verifier caught an earlier version implying more.
    Folding a different defect class into that total would break that sentence.
    Each scan reports its own denominator; `build()` unions them for `clean`.

    🔴 DERIVED, NEVER DECLARED, the same rule the encoding scan follows.  Every
    class below is a query against the database this export just read, so the
    claim expires the moment somebody repairs the rows — which is the whole point
    of the hand-authored-literal lesson this file already carries.
    """
    classes: dict[str, dict] = {}

    def add(key, what, rows):
        for ssys, n in rows:
            c = classes.setdefault(key, {"total": 0, "by_source": {}, "what": what})
            c["total"] += n
            src = BY_SYSTEM.get(ssys)
            if src:
                c["by_source"][src] = c["by_source"].get(src, 0) + n

    # 1. idempotency: a NULL `events.dedup_key` makes the UNIQUE index that
    #    guarantees a re-run is a no-op inert for those rows, so idempotency
    #    depends on the loader remembering — the exact failure DEVIATION 7 exists
    #    to stop.  Named as untouched by the holder corrective pass; still live.
    add("events_missing_dedup_key",
        "mineral_resource_identified events with a NULL dedup_key; the UNIQUE index "
        "that makes a re-run idempotent cannot see them",
        con.execute("""
            SELECT en.source_system, COUNT(*) FROM events ev
              JOIN entities en ON en.entity_id = ev.subject_entity_id
             WHERE ev.event_type = 'mineral_resource_identified'
               AND ev.dedup_key IS NULL GROUP BY 1""").fetchall())

    # 2. a site with no commodity assertion is a dot the map cannot label.
    # 🔴 AND IT IS A DEFECT ONLY FOR THE MINERAL FAMILY.  This check was written
    # when every `asset` in the graph was a mine, where "no commodity" means the
    # loader failed to record why the site is worked.  North Dakota broke that
    # assumption HONESTLY: SESSION-2026-09-03-nd-oilgas-load §5 records 17 well
    # types that carry NO assertion DELIBERATELY — 1,068 salt-water disposal, 850
    # water injection, 183 stratigraphic test, and the rest injection, storage and
    # monitoring wells — because "injection, disposal, water, storage and
    # monitoring wells do not produce a commodity; asserting one as the reason the
    # site is worked would be false."
    # Counting those as defects converts a deliberate under-claim into an error
    # report, which is the precise inversion of what this scan exists to do.  They
    # are counted SEPARATELY below, as a fact, and named in the manifest.
    add("sites_without_a_commodity",
        "mineral-family asset entities carrying no site_commodity_assertion row at "
        "all; oil/gas well types that assert none by design are counted apart",
        con.execute("""
            SELECT en.source_system, COUNT(*) FROM entities en
             WHERE en.entity_type = 'asset'
               AND en.source_system NOT IN ('ndic','kgs')
               AND NOT EXISTS (SELECT 1 FROM site_commodity_assertion s
                                WHERE s.site_entity_id = en.entity_id)
             GROUP BY 1""").fetchall())

    # 3. a located site whose location entity is not a place this export can
    #    resolve would be silently absent from every plane.
    add("sites_with_an_unresolvable_location",
        "mineral_resource_identified events whose location_entity_id has no row in "
        "`locations`, or none at all",
        con.execute("""
            SELECT en.source_system, COUNT(*) FROM events ev
              JOIN entities en ON en.entity_id = ev.subject_entity_id
             WHERE ev.event_type = 'mineral_resource_identified'
               AND (ev.location_entity_id IS NULL
                    OR NOT EXISTS (SELECT 1 FROM locations l
                                    WHERE l.entity_id = ev.location_entity_id))
             GROUP BY 1""").fetchall())

    # the same population, counted and NAMED rather than dropped: an oil/gas site
    # asserting no commodity is a decision the loader made on the source's own
    # vocabulary, and a reader is owed the number either way.
    oilgas_no_commodity = con.execute("""
        SELECT COUNT(*) FROM entities en
         WHERE en.entity_type IN ('asset','permit')
           AND en.source_system IN ('ndic','kgs')
           AND NOT EXISTS (SELECT 1 FROM site_commodity_assertion s
                            WHERE s.site_entity_id = en.entity_id)""").fetchone()[0]

    by_source: dict[str, int] = {}
    for c in classes.values():
        for k, v in c["by_source"].items():
            by_source[k] = by_source.get(k, 0) + v
    return {"classes": classes, "by_source": by_source,
            "oilgas_sites_asserting_no_commodity": {
                "count": oilgas_no_commodity,
                "why": "NOT a defect. 17 ND well types (salt-water disposal, water "
                       "injection, stratigraphic test, CO2/air/gas injection, storage "
                       "and monitoring) assert no commodity BY DESIGN — see "
                       "SESSION-2026-09-03-nd-oilgas-load §5. Asserting one would be "
                       "false. Counted so the number is visible without being "
                       "reported as an error."},
            "total": sum(c["total"] for c in classes.values()),
            "counts": "rows in each named class — a different denominator from the "
                      "encoding scan's display-name total, and never summed with it"}


# ══════════════════════════════════════════════════════════════════════════════
# assembly
# ══════════════════════════════════════════════════════════════════════════════

def stamp_entity_types(con, signals) -> int:
    """Every row the panel can open carries the entity's type twice: `raw_type`
    is `entities.entity_type` verbatim — the value `graph_api.node()` looks up
    and the one the census is keyed on — and `type` is the graph's vocabulary
    name (`NODE_TYPES`) where it has one, the raw value where it does not, for a
    reader.  Returns the number of ids the database does not hold at all, which
    are dead clicks by definition (and land in the census as untyped)."""
    refs, seeds = [], []
    for s in signals:
        if s.get("seed"):
            seeds.append(s)
        for r in routability.entity_refs({k: v for k, v in s.items() if k != "seed"}):
            if r.get("entity_id"):
                refs.append(r)
    want = sorted({r["entity_id"] for r in refs} | {s["seed"] for s in seeds})
    raw = {}
    for i in range(0, len(want), 500):
        chunk = want[i:i + 500]
        raw.update(con.execute(
            f"SELECT entity_id, entity_type FROM entities WHERE entity_id IN "
            f"({','.join('?' * len(chunk))})", chunk).fetchall())
    for r in refs:
        t = raw.get(r["entity_id"])
        if t is not None:
            r["raw_type"] = t
            r["type"] = NODE_TYPES.get(t, t)
    for s in seeds:
        t = raw.get(s["seed"])
        if t is not None:
            s["seed_raw_type"] = t
            s["seed_type"] = NODE_TYPES.get(t, t)
    return len(want) - len(raw)


def build(db_path: str, frontend: str | None = None):
    con = connect(db_path)
    counties, states, city2county = load_geography(con)
    canon = canonical_map(con)

    defects = scan_defects(con)
    com_defects = scan_commodity_defects(con)
    contra = scan_contra(con)
    # 🔴 THE NODE LEGEND IS A CENSUS, NOT A LIST.  The prototype's legend
    # advertised `contract`, `event` and `facility` when the graph held zero of
    # each, and the fix was to hand-edit the list — which is a hand-authored
    # literal, the thing this file spent a session learning not to trust.  Every
    # node type is counted here from `entities`, and the frontend renders only the
    # ones with rows.  `asset` earns its entry with 338; a type that drops to zero
    # loses its entry with no code change.
    node_types = {}
    for etype, n in con.execute(
            "SELECT entity_type, COUNT(*) FROM entities GROUP BY 1"):
        t = NODE_TYPES.get(etype)
        if t:
            node_types[t] = node_types.get(t, 0) + n
    # 🔴 CLEAN IS COMPUTED, NOT CONFIGURED.  A source is clean when its verifier
    # pass has run AND the live database currently shows no defect attributable
    # to it.  Both halves are needed: a pass that ran does not make today's data
    # clean, and clean data does not mean anyone checked.
    # ⚠️ AND THE UNION IS OVER EVERY SCAN, NOT JUST THE ENCODING ONE.  A second
    # defect class that did not feed this set would be measured, printed, and
    # then have no consequence — a number on a dashboard rather than a claim the
    # surface acts on.
    attributed = dict(defects["by_source"])
    for k, v in com_defects["by_source"].items():
        attributed[k] = attributed.get(k, 0) + v
    clean = {k for k, src in SOURCES.items()
             if src.verification == "hardened" and not attributed.get(k)}

    con_sig = contract_signals(con, counties, canon)
    pat_sig, dropped, seats = patent_signals(con, counties, city2county, canon)
    dem_sig = demand_signals(con, counties, city2county, canon, con_sig, seats)
    com_county, com_state, unmappable, unregistered, com_stats = commodity_signals(
        con, counties, states, canon)
    oil_county, oil_stats = oilgas_signals(con, counties, canon)

    county_signals = defaultdict(list)
    for layer in (con_sig, pat_sig, dem_sig):
        for fips, sig in layer.items():
            county_signals[fips].append(sig)
    # 🔴 ADDITIVE, NEVER REPLACING.  A county already carrying a contract and a
    # patent signal gains a THIRD and possibly a FOURTH entry; nothing merges,
    # nothing averages, and no existing signal is displaced.  This is the same
    # cardinality rule the array itself exists to hold, applied to a new layer.
    for fips, by_source in com_county.items():
        for sig in by_source.values():
            county_signals[fips].append(sig)
    # 🔴 A THIRD FAMILY JOINS THE SAME ARRAY, ADDITIVELY.  Nothing merges with the
    # commodity layer and nothing is displaced; a North Dakota county already
    # carrying a patent signal gains an oil/gas one beside it.
    for fips, sig in oil_county.items():
        county_signals[fips].append(sig)
    # a stable, meaningful order: hardened before pending, then by confidence
    # hardened-and-clean first, then by confidence — so the panel never leads
    # with the layer that carries a live defect
    # ⚠️ A NULL CONFIDENCE DOES NOT CRASH AND DOES NOT BECOME 0.0 IN THE PAYLOAD.
    # A commodity signal whose sites carry no assertion at all has
    # `confidence: None`; the first version of this sort raised `TypeError` on the
    # unary minus, found by a fixture county built to hold exactly that site and by
    # nothing else.  Substituting 0.0 would have been this file inventing a
    # measurement to keep a sort happy, and that half IS pinned — a mutant that
    # emits 0.0 is caught.
    # 🔴 THE `1 if c is None else 0` TERM IS NOT OBSERVABLE AND SAYING SO IS THE
    # POINT.  A verifier's mutant that ranked None as 0.0 SURVIVED, and it should
    # have: confidences are non-negative, so "None last" and "None as zero" order
    # identically for every possible input.  The term is kept because it states the
    # intent at the site of the decision, but it is not a behaviour and this file
    # will not claim a test pins it.
    def _order(s):
        c = s["confidence"]
        return (0 if s["source"] in clean else 1,
                1 if c is None else 0, -(c or 0.0), s["source"])
    for sigs in county_signals.values():
        sigs.sort(key=_order)

    # ── `no-signal`, and the first time this export has ever emitted it ───────
    # 🔴 CHECKED-AND-EMPTY IS A DIFFERENT CLAIM FROM NEVER-CHECKED, and until now
    # this export could make neither honestly.  A source with a `sweep_scope` HAS
    # enumerated the places inside it, so a county in that scope carrying no
    # signal from that source is `no-signal` FOR THAT SOURCE — measured, not
    # assumed, and confined to the jurisdiction the sweeper actually regulates.
    # ⚠️ IT IS NOT A WHOLE-CORPUS CLAIM.  Traill was checked by NDIC and by
    # NOBODY ELSE; the other four sources never reached it.  So the row carries
    # `swept_by` and the page renders "checked here, none found" for those
    # families and "did not reach" for the rest — the distinction the NOT REACHED
    # section already exists to hold.  Calling the county flatly "no-signal"
    # without naming who checked would be the overclaim this whole surface
    # refuses.
    no_signal = {}
    for sid, scope in sorted(SWEEP_SCOPES.items()):
        for st in scope:
            # 🔴 A SWEEP IS ONLY CLAIMABLE IF THE SOURCE IS ACTUALLY LOADED.
            # Caught by the fixture, not by reading: `sweep_scope` is a property of
            # the SOURCE, so the first version emitted `no-signal` for all 53 North
            # Dakota counties against any database — including one where the NDIC
            # load had never run and `oil_gas_nd` contributed nothing at all.  That
            # is the worst claim on this surface: "checked here, found none" on the
            # authority of a registry that was never read.  A declared scope is a
            # statement of what the source COULD sweep; the data present is what it
            # DID.  Both are required, and the weaker one wins.
            present = any(fips[:2] == st and any(x["source"] == sid for x in sigs)
                          for fips, sigs in county_signals.items())
            if not present:
                continue
            universe = swept_county_universe(st)
            for fips, nm in sorted(universe.items()):
                if fips in county_signals:
                    continue
                row = no_signal.setdefault(
                    fips, {"name": nm, "swept_by": [], "swept_families": []})
                row["swept_by"].append(sid)
                fam = FAMILY[sid]
                if fam not in row["swept_families"]:
                    row["swept_families"].append(fam)

    # ── states ────────────────────────────────────────────────────────────────
    # 🔴 A STATE'S STATUS IS ITS COUNTIES', NOT AN INDEPENDENT MEASUREMENT.  The
    # prototype's `countyData()` already inherits downward ("a state never checked
    # cannot have checked counties"); this is the same rule read upward, which is
    # the only direction real data supports — nothing checks a state as such.
    state_counties = defaultdict(list)
    for fips in county_signals:
        state_counties[fips[:2]].append(fips)
    # a `no-signal` county is still a county this export has something to say
    # about, so its state file must carry it — otherwise the page falls back to
    # `no-coverage` and the sweep changes nothing a reader can see.
    for fips in no_signal:
        state_counties[fips[:2]].append(fips)

    # 🔴 EVERY SIGNAL, ON EVERY PLANE.  The first version set this on the county
    # arrays only, so the three state-tier signals reached `national.json` with no
    # `clean` key at all and the Pro filter that reads it silently did not apply to
    # them — the same shape as the mutant that dropped `verification` from the
    # national KEEP and disabled a filter with no error.
    for sigs in list(county_signals.values()) + [
            list(by.values()) for by in com_state.values()]:
        for sig in sigs:
            sig["clean"] = sig["source"] in clean

    # ── the routability census: read both ends, refuse rather than assume ─────
    # 🔴 EVERY ROW THE PANEL CAN OPEN GETS ITS TYPE, then the set of types is
    # checked against what the graph API returns AND what the shipped page draws.
    # `main()` refuses to write an export that fails this; see routability.py for
    # the two silent failures that made this a gate rather than a note.
    all_sigs = [s for sigs in county_signals.values() for s in sigs] + \
               [s for by in com_state.values() for s in by.values()]
    unknown_ids = stamp_entity_types(con, all_sigs)
    page = routability.resolve_frontend(frontend, os.path.dirname(os.path.abspath(__file__)))
    with open(page, encoding="utf-8") as f:
        table = routability.route_table(f.read())
    rcensus = routability.census(routability.referenced(all_sigs), table, NODE_TYPES)
    rcensus["frontend"] = {"file": os.path.basename(page), "md5": routability.md5_of(page)}
    rcensus["ids_not_in_entities"] = unknown_ids

    return {
        "routability": rcensus,
        "clean": sorted(clean),
        "contra": contra,
        "node_types": dict(sorted(node_types.items())),
        "defects": defects,
        "commodity_defects": com_defects,
        "counties": dict(county_signals),
        "no_signal": no_signal,
        # 🔴 STATE-TIER SIGNALS ARE A SEPARATE KEY, NOT SMUGGLED INTO `counties`.
        # The prototype's rule — "a state's status is its counties'" — holds for
        # every county-derived fact and is not being repealed.  These three EIA
        # facilities are the first rows in this corpus that assert something about
        # a STATE AS SUCH, and they get their own field so the roll-up rule stays
        # readable and the exception stays visible.
        "state_tier": {ss: list(by.values()) for ss, by in com_state.items()},
        "unmappable": unmappable,
        "unregistered_sites": unregistered,
        "commodity_stats": com_stats,
        "oilgas_stats": oil_stats,
        "site_points": site_points(con),
        "state_counties": dict(state_counties),
        "county_names": {v[0]: v[1] for v in counties.values()},
        "county_kind": {v[0]: v[2] for v in counties.values()},
        "state_names": {v[0]: v[1] for v in states.values()},
        "stats": {
            "counties_in_graph": len(counties),
            "counties_with_signal": len(county_signals),
            "counties_contract": len(con_sig),
            "counties_patent": len(pat_sig),
            "counties_demand": len(dem_sig),
            "counties_convergent": sum(1 for s in county_signals.values() if len(s) > 1),
            # 🔴 AND A SECOND CONVERGENCE NUMBER, BECAUSE THE FIRST ONE NOW
            # OVERCOUNTS.  `counties_convergent` counts a county with two or more
            # signals.  MSHA and EIA are two SOURCES but they are not two
            # INDEPENDENT observers: they describe overlapping physical sites on
            # purpose — `White Mesa Mill` is deliberately loaded twice, once under
            # each anchor — so a county lit by both is one fact seen twice, not two
            # facts agreeing.  This second figure collapses the commodity family to
            # one before counting, and it is the honest denominator for "sources
            # converge here".  Both are emitted; neither substitutes for the other.
            "counties_convergent_independent": sum(
                1 for s in county_signals.values()
                if len({FAMILY[x["source"]] for x in s}) > 1),
            "counties_commodity": com_stats["counties"],
            # named explicitly rather than folded into `counties_with_signal`, so
            # the delta this work order adds is legible instead of absorbed
            "counties_oilgas": oil_stats["counties"],
            "patent_mentions_county_tier_dropped": dropped,
            "canonical_links": len(canon),
            "entity_names_with_encoding_defect": defects["total"],
            "…reversible": defects["reversible"],
            "…irreversible": defects["irreversible"],
        },
    }, con


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "map-v1"))
    ap.add_argument("--frontend", default=None,
                    help="the page this export will be served with; its SYM/SRC_TAG tables "
                         "are the route table the census checks (default: the sibling page)")
    args = ap.parse_args()

    data, con = build(args.db, args.frontend)
    # 🔴 REFUSED BEFORE A SINGLE FILE IS WRITTEN.  An export carrying a type the
    # page cannot open is the Block 11 `asset` failure again; it does not get to
    # exist on disk where a deploy could pick it up.  The way through is a route
    # in the page, or an entry in `routability.ALLOWLIST` with a reason.
    why = routability.refusal(data["routability"])
    if why:
        rt = data["routability"]
        print(f"🔴 EXPORT REFUSED — {why}")
        print(f"   route table (graph_api ∩ page {rt['frontend']['file']}): {rt['node_route_table']}")
        print(f"   types referenced: {rt['types_referenced']}")
        print(f"   nothing written to {args.out}")
        sys.exit(2)
    os.makedirs(os.path.join(args.out, "county"), exist_ok=True)

    # ── national.json ─────────────────────────────────────────────────────────
    # The national plane needs everything `stateMarkers`/`passes` read — events,
    # confidence, tier, ev, seed, source — and nothing else.  `detail` is the
    # county plane's job, and shipping it twice would triple the first payload.
    KEEP = ("source", "shape", "confidence", "events", "tier", "ev", "seed",
            "verification", "clean")
    national = {}
    for st, fipses in data["state_counties"].items():
        sigs = []
        # 🔴 `state_counties` NOW CARRIES `no-signal` COUNTIES TOO, and they have no
        # signal array to roll up.  Counting them here would have made
        # `counties_with_signal` include a county whose whole point is that it has
        # none — the status bar's own headline, inflated by the honesty feature.
        with_signal = [f for f in fipses if f in data["counties"]]
        for f in with_signal:
            for sig in data["counties"][f]:
                row = {k: sig[k] for k in KEEP if k in sig}
                row["fips"] = f
                sigs.append(row)
        national[st] = {
            # a state holding only `no-signal` counties is still a state this
            # export has checked; it is not `has-signal` and it is not dark
            "status": "has-signal" if with_signal else "no-signal",
            "counties_with_signal": len(with_signal),
            "counties_no_signal": len(fipses) - len(with_signal),
            "signals": sigs,
        }
    for st in data["state_names"]:
        national.setdefault(st, {"status": "no-coverage", "counties_with_signal": 0, "signals": []})

    # ── state-tier signals ────────────────────────────────────────────────────
    # 🔴 A SEPARATE KEY, AND THE STATUS RULE IS WIDENED RATHER THAN BYPASSED.  The
    # roll-up rule ("a state's status is its counties'") was written when nothing
    # in the corpus checked a state as such.  Three EIA facilities now do — their
    # footprint spans two counties and the loader recorded state precision — so a
    # state can hold a signal with no county under it.  `counties_with_signal`
    # keeps counting COUNTIES and is untouched; `status` becomes has-signal if
    # either kind is present, because a state that holds a real facility is not
    # "never checked".
    # ⚠️ LATENT TODAY: both states carrying one (46 South Dakota, 56 Wyoming)
    # already have county-tier commodity signals, so no state's status changes.
    # A branch that only future data can reach is still a branch, and it is pinned
    # by a fixture that reaches it.
    for st, sigs in data["state_tier"].items():
        rows = []
        for sig in sigs:
            row = {k: sig[k] for k in KEEP if k in sig}
            row["precision_tier"] = "state"
            row["fips"] = None
            row["detail"] = sig["detail"]
            row["detail_rest"] = sig.get("detail_rest", [])
            # the seed's type ships with the seed: these three rows are the only
            # national-plane signals that carry openable detail, so they are in
            # the census and must be censusable from what actually ships
            row["seed_type"] = sig.get("seed_type")
            row["seed_raw_type"] = sig.get("seed_raw_type")
            row["detail_total"] = sig["detail_total"]
            rows.append(row)
        entry = national.setdefault(
            st, {"status": "no-coverage", "counties_with_signal": 0, "signals": []})
        entry["state_tier_signals"] = rows
        entry["status"] = "has-signal"

    with open(os.path.join(args.out, "national.json"), "w") as f:
        json.dump({"states": national}, f, separators=(",", ":"))

    # ── county/<SS>.json ──────────────────────────────────────────────────────
    for st, fipses in data["state_counties"].items():
        payload = {}
        for f in fipses:
            ns = data["no_signal"].get(f)
            if ns is not None:
                # 🔴 A ROW THAT SAYS WHO CHECKED.  `no-signal` with no attribution
                # would read as "the whole corpus checked here", which is false —
                # exactly one family did.  The page renders the rest as "did not
                # reach" from its own NOT REACHED census.
                payload[f] = {"status": "no-signal", "fips": f,
                              "name": data["county_names"].get(f, ns["name"]),
                              "kind": data["county_kind"].get(f),
                              "signals": [],
                              "swept_by": ns["swept_by"],
                              "swept_families": ns["swept_families"]}
                continue
            nm = data["county_names"].get(f, f)
            # 🔴 THE COUNTY CARRIES ITS OWN FIPS.  The payload is keyed by it, but
            # the ROW did not hold it, and the page needs it to ask whether this
            # county sits inside a source's `sweep_scope` — "checked and empty"
            # and "never looked" are different sentences and only the FIPS says
            # which one this county has earned.
            row = {"status": "has-signal", "fips": f, "name": nm,
                   "kind": data["county_kind"].get(f),
                   "signals": data["counties"][f]}
            # 🔴 SHOWN, NOT REPAIRED.  Quietly writing the corrected name here
            # would make this map the place a live graph defect goes to die.  The
            # stored name is what renders; the correction rides alongside it with
            # its cause named.
            fix = mojibake(nm)
            if fix:
                row["name_defect"] = {"stored": nm, "should_be": fix,
                                      "cause": data["defects"]["cause"]}
            payload[f] = row
        with open(os.path.join(args.out, "county", f"{st}.json"), "w") as f:
            json.dump({"state": st, "counties": payload}, f, separators=(",", ":"))

    # ── sites-v1.json — the canvas layer's points, fetched only when toggled on ──
    with open(os.path.join(args.out, "sites-v1.json"), "w") as f:
        json.dump(data["site_points"], f, separators=(",", ":"))

    # ── manifest.json ─────────────────────────────────────────────────────────
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db": {"path": os.path.abspath(args.db),
               "bytes": os.path.getsize(args.db),
               "change_counter": change_counter(args.db)},
        "coverage": {
            "states_emitted": ["has-signal", "no-coverage"],
            # 🔴 COUNTY STATES ARE NOT STATE STATES, AND `no-signal` NOW OCCURS.
            # Until `oil_gas_nd` this export emitted two of the grammar's three
            # county states and `no-signal` was unreachable — recorded here as a
            # fact about the corpus, not a rendering choice.  It is reachable now,
            # inside a declared jurisdiction and nowhere else.
            # 🔴 DERIVED, NOT DECLARED — and the first version of these two lines
            # were literals (`["has-signal","no-signal","no-coverage"]` and `True`),
            # which is the hand-authored-claim failure this module has a whole
            # docstring about.  A fixture with no swept source present made them
            # false immediately: it emits no `no-signal` county at all.
            "counties_emitted": (["has-signal", "no-signal", "no-coverage"]
                                 if data["no_signal"] else
                                 ["has-signal", "no-coverage"]),
            "no_signal_emitted": bool(data["no_signal"]),
            "no_signal_counties": len(data["no_signal"]),
            # which source swept where.  The PAGE needs this per county: a family
            # absent from a county INSIDE a scope was checked and found nothing;
            # the same family absent OUTSIDE it simply never looked, and the two
            # must not render the same sentence.
            "sweep_scopes": {k: list(v) for k, v in sorted(SWEEP_SCOPES.items())},
            "swept_families": sorted({FAMILY[k] for k in SWEEP_SCOPES}),
            # 🔴 THIS SENTENCE SHIPS TO A USER, AND IT WAS FALSE FOR THE LENGTH OF
            # THIS WORK ORDER.  It read "None of the three sources this map reads
            # enumerates places" — wrong on the count once there were five, and
            # wrong on the substance, because Stage 1's whole finding is that MSHA
            # DOES enumerate places.  `map_sources.py` said so while this said the
            # opposite, and it was this one the frontend rendered.
            "why": "Four of the five sources this map reads never enumerate places at all "
                   "— each checks an entity that turns out to have one, so a county they "
                   "miss was never asked about. The fifth, MSHA's mine registry, DOES "
                   "enumerate places: registration is compelled by federal law across all "
                   "55 state and territory codes. But the slice loaded here is only its "
                   "PRIMARY_CANVASS='Metal' mines, and 156 counties hold a live MSHA "
                   "registration that filter cannot see — so 'checked here, found nothing' "
                   "is still not a claim any row behind this map supports.",
            "not_claimed": "That nothing in the graph can express a sweep. "
                           "loader/load_phase0.py:load_coverage() enumerates every state "
                           "location and writes no_signal; this export never reads "
                           "coverage_log, and that loader's output would today be a false "
                           "negative for 50 of 53 states, whose congressional trades have "
                           "moved to district precision.",
            "any_source_sweeps_geography": not NO_SOURCE_SWEEPS,
        },
        "contra": {"emitted": False, "state": "not-screenable",
                   "statement": CONTRA_STATEMENT, "census": data["contra"]},
        "clean_sources": data["clean"],
        "node_types": data["node_types"],
        # 🔴 THE ROUTABILITY CENSUS, DISCLOSED.  `unroutable_types` is empty in any
        # export that exists, because main() refuses to write one where it is not;
        # the field is here so that fact is checkable from the manifest alone, and
        # so a test can re-derive it against the shipped page and the shipped rows.
        "unroutable_types": data["routability"]["unroutable_types"],
        "routability": data["routability"],
        # 🔴 THE FRONTEND NEEDS THIS TO COUNT CONVERGENCE HONESTLY.  Its
        # `Min converging sources` control and its inspector header both count
        # DISTINCT SOURCES, which was the same thing as distinct observers until
        # this export gained two sources that describe the same physical sites on
        # purpose.  Shipping the family map is what lets the UI collapse them
        # instead of re-deriving the rule from source-id string shapes.
        "source_families": FAMILY,
        "defects": {k: v for k, v in data["defects"].items() if k != "rows"},
        "commodity_defects": data["commodity_defects"],
        "commodity": {
            "stats": data["commodity_stats"],
            # 🔴 NAMED, NOT SILENTLY DROPPED.  Every facility this export cannot
            # place on a county plane is listed here with the reason and with the
            # counties its own source published, so the gap is auditable from the
            # manifest alone.  No centroid is derived for any of them.
            "unmappable_at_county_precision": data["unmappable"],
            "unregistered_sites": data["unregistered_sites"],
            "no_fallback_centroid": True,
            "families": {k: v for k, v in FAMILY.items() if v == "commodity"},
            "never_merged": (
                "MSHA and EIA-851A are two sources and one family. They genuinely "
                "contradict each other about the same physical sites — 5 of the 6 "
                "facilities EIA reports Operating at end-2025 are Abandoned in MSHA — "
                "so they are emitted as separate signals, never combined into one "
                "confidence, and `counties_convergent_independent` collapses them "
                "before counting convergence."),
        },
        # 🔴 THE BOUNDED REMAINDER, DECLARED WHERE A TEST CAN READ IT.  Oil/gas is
        # the ONLY layer whose `detail_rest` is capped, because McKenzie County
        # alone would otherwise ship 4.4 MB of well rows.  The cap was real in the
        # export and absent from the manifest, so the invariant every other layer
        # keeps — "rest ships the whole remainder" — could only be checked by
        # knowing a constant that lives in this file, and the census test that
        # enforces it failed with no way to tell a deliberate bound from a bug.
        # It is a disclosure now: a test asserts the DECLARED cap instead of
        # assuming completeness, and `rows_unshipped` is the same number the page
        # states through `unshipped`/`hiddenNote()`.
        "oil_gas": {
            "stats": data["oilgas_stats"],
            "rest_is_capped": True,
            "families": {k: v for k, v in FAMILY.items() if v == "oil_gas"},
            "bounded_rest": (
                "`detail_rest` for oil/gas ships at most `rest_cap` rows plus, always, "
                "the lowest-confidence well in the county — the well that DECIDES the "
                "county's MIN, which a straight positional cap dropped for 2 of 52 "
                "counties, leaving a panel that could not justify its own number. "
                "Every other layer ships its whole remainder. `detail_total` stays the "
                "true well count, and the page renders `detail_total - (detail + "
                "detail_rest)` as an explicit sentence rather than implying the list "
                "is the whole population."),
        },
        "sources": {k: {"label": s.label, "frame": s.frame,
                        "sweeps_geography": s.sweeps_geography,
                        "frame_evidence": s.frame_evidence,
                        "verification": s.verification,
                        "verification_note": s.verification_note,
                        "tier": s.tier, "evidence": s.evidence}
                    for k, s in SOURCES.items()},
        "stats": data["stats"],
    }
    with open(os.path.join(args.out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    print(json.dumps(data["stats"], indent=2))
    d = data["defects"]
    print(f"\nclean sources: {', '.join(data['clean']) or 'NONE'}")
    print(f"live entity-name defects: {d['total']} "
          f"({d['reversible']} reversible, {d['irreversible']} irreversible; "
          f"{', '.join(f'{k}={v}' for k, v in sorted(d['by_source'].items())) or 'unattributed'})")
    for r in d["rows"]:
        if r["kind"] == "irreversible":
            print(f"    🔴 IRREVERSIBLE [{r['entity_type']}/{r['source_system']}] {r['stored']!r}")
    cd = data["commodity_defects"]
    cs = data["commodity_stats"]
    print(f"\ncommodity: {cs['sites']} sites  "
          f"{cs['counties']} counties ({cs['counties_msha']} msha / {cs['counties_eia']} eia / "
          f"{cs['counties_both_commodity_sources']} both)  "
          f"{cs['state_tier_facilities']} state-tier  "
          f"{cs['unmappable_at_county_precision']} unmappable at county precision")
    print(f"           holders: {cs['sites_with_a_holder_edge']} sites with an edge, "
          f"{cs['sites_with_a_cik_resolved_holder']} with a CIK-resolved holder")
    for k, v in sorted(cd["classes"].items()):
        print(f"    🔴 {k}: {v['total']}  ({', '.join(f'{a}={b}' for a, b in sorted(v['by_source'].items())) or 'unattributed'})")
    rt = data["routability"]
    print(f"\nroutability: {len(rt['types_referenced'])} entity types referenced "
          f"{sorted(rt['types_referenced'])}, route table {rt['node_route_table']}, "
          f"unroutable {rt['unroutable_types']}, allow-listed {list(rt['allowlisted'])}, "
          f"untyped rows {rt['untyped_rows']}, nameless rows {rt['nameless_rows']}, "
          f"idless rows {rt['idless_rows']}, "
          f"ids not in entities {rt['ids_not_in_entities']}"
          f" — page {rt['frontend']['file']} md5 {rt['frontend']['md5']}")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
