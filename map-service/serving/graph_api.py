#!/usr/bin/env python3
"""Block 11 — bounded, real entity neighbourhoods for the map's graph plane.

Replaces the prototype's synthetic `UNIVERSE`/`ADJ`/`neighborsOf()`.

────────────────────────────────────────────────────────────────────────────────
--- why this is not a static export ---

The synthetic universe held ~90 nodes and one click could add at most a handful.
The real graph holds 234,787 entities and 249,681 edges, and **one click on Apple
Inc. would add 25,855 nodes**.  The 383 seeds this map can reach have 107,048
edges between them before a single expansion.  A static per-node export of the
click-reachable closure is therefore not bounded by anything, so the graph plane
is served by a query instead — the "dedicated lightweight API" the directive
lists as an option.  The map planes stay static JSON.

--- what bounds an expansion, and what is disclosed when it bites ---

`K` neighbours per expansion, round-robin across relationship types so that a
company's single agency edge is never buried under 25,000 patents, each bucket
ordered by the graph's own `confidence_score`.

🔴 THE CAP IS DISCLOSED, NOT HIDDEN.  Every response carries the node's TRUE
degree alongside the number actually returned.  The prototype already draws ghost
dots for unexpanded neighbours and counts them in the status bar; that grammar is
exactly the affordance a cap needs, so it is reused rather than replaced.  A node
never silently loses connections — it says how many it is not showing.

--- canonical roll-up ---

Expansion happens on the CANONICAL entity.  `GENERAL DYNAMICS CORP` carries 359
edges of its own and 248 more through `General Dynamics Corporation`, the
PatentsView stray that `entity_canonical` already resolves onto it; showing only
the former would present a fifth of the company's real patent estate as the whole
of it.  Absorbed edges keep a `via` naming the stray they came through, so the
roll-up is visible rather than laundered — the same disclosure Block 8's report
makes with its `⤷ merged from` lines.

--- location and research nodes ---

`edges` never touches a location: RE-MEASURED against the live graph for the
commodity wiring — 0 of 250,433 rows has a location entity at either end, so the
finding stands and the figure has moved (it was 249,681 when this file was
written).  Places reach the graph through `events.location_entity_id` and
`patent_location`, so this module derives those relations explicitly.  They are
real rows with real provenance, and each one says which.  That is what makes
`location` a first-class node type here (End state #4) rather than a legend entry
with nothing behind it.

⚠️ AND `edges` DOES NOW TOUCH AN `asset`: 730 rows, all `ownership` or
`contractor`, joining a mineral site to its holder.  That is the one relation the
commodity layer did NOT have to derive — it was already in the shared table, and
the graph plane was already counting it into `degree` while dropping every asset
node from the neighbour list.  See `TYPE_MAP`.  The site->PLACE relation is
derived here, like the other two, from `events.location_entity_id` on
`mineral_resource_identified`.
"""
from __future__ import annotations

import datetime
import heapq
import json
import os
import sqlite3
import sys
import threading
import time
from collections import defaultdict, OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 🔴 THE NODE VOCABULARY LIVES IN THE REGISTRY, NOT HERE.  The export needs the
# same mapping to census which node types actually have rows, and the legend is
# built from that census — so a second copy in this file would be two
# definitions of one rule, which is exactly how the map plane and the graph
# plane end up disagreeing about what a `location` is.
from map_sources import SOURCES, NODE_TYPES as TYPE_MAP
from export_map import mojibake

DEFAULT_DB = os.environ.get("OSINT_DB") or os.path.expanduser(
    "~/dev/osint-graph/data/osint.db")

# 🔴 THE EXPANSION CAP LIVES HERE, NOT IN THE SERVER, and moving it is the point.
# `serve_map.py` clamped `k` to 200 while the frontend asked for 40, so the only
# thing between a caller and a 200-row expansion was a number in a process a
# deployment might not even use.  A second consumer now mounts this module
# directly, and a bound each consumer has to remember is not a bound.
#
# ⚠️ AND IT DOES NOT BOUND THE COST.  Measured on the live graph, `Apple Inc.`
# (26,435 relations):
#
#     k=1  128.5 ms    k=5  121.2 ms    k=40  122.5 ms    k=200  124.7 ms
#
# The cap is applied AFTER `_raw_edges()` has materialised every relation, so it
# bounds the RESPONSE SIZE and nothing else — for a hostile caller and for the
# frontend alike.  Bounding the WORK needs a bounded fetch; this constant is not
# it and must not be read as it.
MAX_K = 40

REL_LABEL = {
    "patent_ownership": "assignee of",
    "patent_inventor": "inventor on",
    "government_contract": "contract with",
    "personnel_network": "personnel link",
    "lobbying": "lobbying",
    "ownership": "owns",
    "contractor": "operator of",
    # derived, not from `edges` — see the module docstring
    "place_of_performance": "work performed in",
    "patent_address": "address on patent",
    "within": "within",
    "mineral_site_location": "site located in",
}

# 🔴 DIRECTION-AWARE LABELS, BECAUSE `ownership` IS DIRECTIONALLY AMBIGUOUS HERE
# AND THE CORRECTIVE PASS RECORDED THAT AS A KNOWN HAZARD.  `entity_a` is the
# OWNED thing for the msha/eia site families and the OWNER for the corrective
# parent->subsidiary family.  A single label per relationship type therefore reads
# backwards for one of them: standing on a mine and seeing "owns Energy Fuels Inc"
# is the graph telling the reader the opposite of what the row says.  These labels
# are chosen by which END of the edge the viewer is standing on, which is a fact
# this module already knows and was throwing away.
#
# ⚠️ THREE PRE-EXISTING RELATIONS READ BACKWARDS THE SAME WAY AND ARE DELIBERATELY
# NOT CHANGED HERE.  Standing on a county today, `within` says "within" for a
# containment that runs the other way, `place_of_performance` says "work performed
# in" about the companies working THERE, and `patent_address` says "address on
# patent" about the patents addressed there.  They belong to the contract and
# patent layers, they are visible on this surface, and correcting them is a
# one-line addition to this dict in a diff that owns those layers.  Surfaced, not
# repaired — the same rule the 61 mis-decoded city names were held to.
REL_LABEL_REVERSE = {
    "ownership": "owned by",
    "contractor": "operated by",
    "mineral_site_location": "site here",
}

# derived relations carry the confidence of the row they are derived FROM, never
# a number invented here
# 🔴 READ, NOT RESTATED.  A mutant flipping `within` to 0.05 SURVIVED the suite,
# because both `within` branches wrote the literal 1.0 and this dict was decorative.
# A constant nothing reads documents an intention rather than a behaviour.
DERIVED_CONF = {
    "patent_address": 0.9,      # == every patent_ownership edge in the graph
    "within": 1.0,              # Census county/state hierarchy
}


class GraphAPI:
    def __init__(self, db_path: str = DEFAULT_DB, cache_size: int = 0):
        # 🔴 OFF BY DEFAULT, ON FOR A SHARED PROCESS.  A cache is only sound
        # against a database that does not change under it.  In development this
        # module reads the live `osint.db`, which other sessions write, so caching
        # there would serve a stale neighbourhood with nothing to notice it.  A
        # deployment reads a STATIC SNAPSHOT replaced as a whole file, so the
        # assumption holds there — and the caller has to opt in and say so.
        self._cache: OrderedDict[tuple, dict] | None = (
            OrderedDict() if cache_size > 0 else None)
        self._cache_size = cache_size
        self._lock = threading.Lock()
        self.db = db_path
        # 🔴 ONE CONNECTION PER THREAD, AND THIS IS A DEFECT FOUND IN PRODUCTION-
        # SHAPED LOAD, NOT A PRECAUTION.  This module used to hold ONE
        # `sqlite3.Connection` with `check_same_thread=False`.  That flag disables
        # the *guard*; it does not make a connection safe to use from two threads
        # at once.  Mounted into a FastAPI app — where every sync route runs in a
        # threadpool — eight concurrent expansions of one high-degree entity
        # produced `sqlite3.InterfaceError` and HTTP 500, and dragged the HOST
        # APPLICATION'S own pages from 2.3 ms to 51.6 ms while doing it.  A single
        # request never showed it and 24 short concurrent requests never showed it;
        # it took eight concurrent 120 ms queries.
        self._local = threading.local()
        self._canon = {r[0]: r[1] for r in self.con.execute(
            "SELECT entity_id, canonical_entity_id FROM entity_canonical")}
        self._strays = defaultdict(list)
        for stray, parent in self._canon.items():
            self._strays[parent].append(stray)
        # 🔴 THE PRECOMPUTED CENSUS IS USED ONLY IF THE DATABASE CAN PROVE IT IS
        # ITS OWN.  Presence of the table is not enough — a truncated or
        # half-written one would make every DISCLOSED count quietly wrong, which
        # is the single worst thing this module can do.  `build_census()` seals it
        # in `entity_census_seal` with its own row count AND the row counts of
        # every table it derives from; if any of those has moved, the fast path is
        # refused outright and every request computes live.  Slow and right beats
        # fast and wrong.
        self._census_misses = 0
        self._census_tbl = self._census_table_is_trustworthy()

    @property
    def con(self) -> sqlite3.Connection:
        """This thread's read-only handle, opened on first use.

        A PROPERTY rather than a rename, so every existing caller — including the
        test suite, which reaches for `g.con` directly — keeps working while the
        sharing stops.  `mode=ro` is re-asserted on each one: the read-only
        guarantee is a property of every handle, not of the first one."""
        c = getattr(self._local, "con", None)
        if c is None:
            c = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True,
                                check_same_thread=False)
            c.row_factory = sqlite3.Row
            self._local.con = c
        return c

    # ── identity ─────────────────────────────────────────────────────────────
    def canonical(self, eid: str) -> str:
        return self._canon.get(eid, eid)

    def group(self, eid: str) -> list[str]:
        """The canonical id plus every stray that resolves onto it."""
        c = self.canonical(eid)
        return [c] + sorted(self._strays.get(c, ()))

    def node(self, eid: str) -> dict | None:
        r = self.con.execute(
            "SELECT entity_id, entity_type, display_name, canonical_anchor_type, "
            "canonical_anchor_value, source_system FROM entities WHERE entity_id=?",
            (eid,)).fetchone()
        if r is None:
            return None
        t = TYPE_MAP.get(r["entity_type"])
        if t is None:
            return None
        n = {"uid": r["entity_id"], "type": t, "label": r["display_name"],
             "raw_type": r["entity_type"], "source_system": r["source_system"]}
        # 🔴 THE GRAPH PLANE IS WHERE THESE ACTUALLY RENDER.  61 place names in the
        # live graph are UTF-8 mis-decoded — `MÃ¼nchen, DE`, `GieÎ²en, DE`,
        # `KÃ¸benhavn Ã˜, DK` — and all but a handful are CITIES, which the county
        # export never touches.  Marking them only on the map plane meant the one
        # surface that shows them said nothing.  Shown as stored, corrected
        # alongside, never substituted.
        fix = mojibake(r["display_name"])
        if fix:
            n["label_defect"] = {"stored": r["display_name"], "should_be": fix}
        if r["canonical_anchor_type"]:
            n["anchor"] = f'{r["canonical_anchor_type"]}:{r["canonical_anchor_value"]}'
        if eid in self._strays:
            n["rolled_up_from"] = [
                {"entity_id": s["entity_id"], "name": s["nm"],
                 "match_basis": s["match_basis"], "confidence": s["confidence"]}
                for s in self.con.execute(
                    "SELECT ec.entity_id, ec.match_basis, ec.confidence, e.display_name nm "
                    "FROM entity_canonical ec JOIN entities e ON e.entity_id=ec.entity_id "
                    "WHERE ec.canonical_entity_id=? ORDER BY e.display_name", (eid,))]
        if r["entity_type"] == "location":
            lr = self.con.execute(
                "SELECT location_type, parent_location_id FROM locations WHERE entity_id=?",
                (eid,)).fetchone()
            if lr:
                n["location_type"] = lr["location_type"]
        return n

    # ── neighbourhood ────────────────────────────────────────────────────────
    def _raw_edges(self, ids: list[str]) -> list[dict]:
        """Every relation touching any member of the canonical group, from the
        three places relations actually live."""
        q = ",".join("?" * len(ids))
        out = []

        for r in self.con.execute(f"""
            SELECT e.edge_id, e.relationship_type rel, e.entity_a_id a, e.entity_b_id b,
                   e.confidence_score conf, e.source_reliability_tier tier,
                   e.direct_or_inferred ev, e.source_type, e.source_url,
                   e.contradicting_sources contra, e.human_verification_status hvs,
                   ea.entity_type a_type
              FROM edges e JOIN entities ea ON ea.entity_id = e.entity_a_id
             WHERE e.entity_a_id IN ({q}) OR e.entity_b_id IN ({q})""",
                tuple(ids) * 2):
            forward = r["a"] in ids
            mine = r["a"] if forward else r["b"]
            other = r["b"] if forward else r["a"]
            # 🔴 `ownership` POINTS BOTH WAYS IN THIS GRAPH AND A PER-DIRECTION
            # LABEL ALONE IS NOT ENOUGH.  The corrective pass recorded it: for the
            # msha/eia site families `entity_a` is the OWNED thing, for the
            # corrective parent->subsidiary family it is the OWNER.  So "which end
            # am I on" does not determine "which way does the verb run".  The
            # disambiguator is the type of `entity_a` — an `asset` in entity_a is
            # always the owned side — which is read from the data rather than from
            # a list of source_id strings that would go stale the first time a
            # third family lands.
            # ⚠️ Caught by a worked example, not by review: standing on the
            # `Energy Queen` mine, the first version rendered "owns Energy Fuels
            # Inc" — the graph telling the reader the exact opposite of the row.
            label_forward = forward
            # 🔴 `permit` TOO, AND ITS ABSENCE WAS THE SAME BUG ONE TYPE OVER.
            # The `asset` test above was written when a site was always a mine.
            # North Dakota writes 6,944 `contractor` edges whose `entity_a` is a
            # `permit` — structurally identical to the asset rows, same direction,
            # same relation — so without `permit` here every one of them renders
            # the verb BACKWARDS: standing on a permitted well, "PERMIT 7 works
            # CONTINENTAL RESOURCES". An independent verifier confirmed the shape
            # on live edges rather than inferring it.
            if r["rel"] in ("ownership", "contractor") and r["a_type"] in ("asset", "permit"):
                label_forward = not forward
            out.append({"rel": r["rel"], "other": other, "confidence": r["conf"],
                        "tier": r["tier"], "ev": r["ev"], "source_type": r["source_type"],
                        "source_url": r["source_url"], "via": mine,
                        # which END the viewer stands on, and which way the verb
                        # runs from there — see REL_LABEL_REVERSE
                        "forward": forward, "label_forward": label_forward,
                        "edge_id": r["edge_id"], "verification": "hardened",
                        "contradicting": r["contra"], "human_status": r["hvs"]})

        # contract place of performance — a real column on `events`, not an edge
        for r in self.con.execute(f"""
            SELECT ev.event_id, ev.subject_entity_id subj, ev.location_entity_id loc,
                   ev.event_timestamp ts, ev.geospatial_precision prec,
                   (SELECT MIN(e.confidence_score) FROM edges e
                     WHERE e.event_id=ev.event_id AND e.relationship_type='government_contract') conf
              FROM events ev
             WHERE ev.event_type='government_contract_awarded'
               AND ev.location_entity_id IS NOT NULL
               AND ev.subject_entity_id IN ({q})""", tuple(ids)):
            out.append({"rel": "place_of_performance", "other": r["loc"],
                        "confidence": r["conf"] if r["conf"] is not None else 0.6,
                        "tier": 1, "ev": "direct", "source_type": "usaspending",
                        "source_url": None, "via": r["subj"], "edge_id": "ev:" + r["event_id"],
                        "verification": "hardened", "date": r["ts"],
                        "precision": r["prec"], "contradicting": "[]",
                        "human_status": "unverified"})

        # patent addresses — the pending-verification layer, marked as such
        for r in self.con.execute(f"""
            SELECT pl.patent_location_id, pl.patent_entity_id pid, pl.location_entity_id loc,
                   pl.role, pl.geospatial_precision prec
              FROM patent_location pl WHERE pl.patent_entity_id IN ({q})
               AND pl.location_entity_id IS NOT NULL""", tuple(ids)):
            out.append({"rel": "patent_address", "other": r["loc"],
                        "confidence": DERIVED_CONF["patent_address"], "tier": 1,
                        "ev": "direct", "source_type": "patentsview",
                        "source_url": None, "via": r["pid"],
                        "edge_id": "pl:" + r["patent_location_id"],
                        # the one place the patent layer's real state is decided
                        "verification": SOURCES["patent"].verification,
                        "role": r["role"],
                        "precision": r["prec"], "contradicting": "[]",
                        "human_status": "unverified"})

        # 🔴 BOTH DIRECTIONS, OR A COUNTY IS A DEAD END.  The first cut queried
        # only company->place and patent->place, so clicking Sagadahoc showed its
        # three towns and nothing else — the one question a place node exists to
        # answer ("who is here?") returned nothing.  A relation you can only
        # traverse one way is not in the graph, it is in the query.
        for r in self.con.execute(f"""
            SELECT ev.event_id, ev.subject_entity_id subj, ev.location_entity_id loc,
                   ev.event_timestamp ts, ev.geospatial_precision prec,
                   (SELECT MIN(e.confidence_score) FROM edges e
                     WHERE e.event_id=ev.event_id AND e.relationship_type='government_contract') conf
              FROM events ev
             WHERE ev.event_type='government_contract_awarded'
               AND ev.location_entity_id IN ({q})""", tuple(ids)):
            out.append({"rel": "place_of_performance", "other": r["subj"],
                        "confidence": r["conf"] if r["conf"] is not None else 0.6,
                        "tier": 1, "ev": "direct", "source_type": "usaspending",
                        "source_url": None, "via": r["loc"], "edge_id": "evr:" + r["event_id"],
                        "verification": "hardened", "date": r["ts"],
                        "precision": r["prec"], "contradicting": "[]",
                        "human_status": "unverified"})

        for r in self.con.execute(f"""
            SELECT pl.patent_location_id, pl.patent_entity_id pid, pl.location_entity_id loc,
                   pl.role, pl.geospatial_precision prec
              FROM patent_location pl WHERE pl.location_entity_id IN ({q})""", tuple(ids)):
            out.append({"rel": "patent_address", "other": r["pid"],
                        "confidence": DERIVED_CONF["patent_address"], "tier": 1,
                        "ev": "direct", "source_type": "patentsview",
                        "source_url": None, "via": r["loc"],
                        "edge_id": "plr:" + r["patent_location_id"],
                        "verification": SOURCES["patent"].verification,
                        "role": r["role"],
                        "precision": r["prec"], "contradicting": "[]",
                        "human_status": "unverified"})

        # 🔴 THE MINERAL SITE'S PLACE — DERIVED, AND IN BOTH DIRECTIONS, because a
        # county that cannot answer "what is mined here?" is the same dead end the
        # first cut left for contracts.  `edges` already carries the site->HOLDER
        # relation (730 rows); it is the site->PLACE relation that lives on
        # `events.location_entity_id`, exactly like a contract's.
        # ⚠️ NO CONFIDENCE IS INVENTED.  `events` has no confidence column and there
        # is no edge behind these rows, so the site's own commodity assertion
        # supplies it — MIN across the site's assertions, the same rule the county
        # signal uses — and NULL rather than a default if the site has none.
        for _fwd, _where in ((True, "ev.subject_entity_id"), (False, "ev.location_entity_id")):
            for r in self.con.execute(f"""
                SELECT ev.event_id, ev.subject_entity_id subj, ev.location_entity_id loc,
                       ev.event_timestamp ts, ev.geospatial_precision prec,
                       (SELECT MIN(s.confidence_score) FROM site_commodity_assertion s
                         WHERE s.site_entity_id = ev.subject_entity_id) conf,
                       (SELECT MIN(s.source_reliability_tier) FROM site_commodity_assertion s
                         WHERE s.site_entity_id = ev.subject_entity_id) tier
                  FROM events ev
                 WHERE ev.event_type IN ('mineral_resource_identified','drilling_activity')
                   AND ev.location_entity_id IS NOT NULL
                   AND {_where} IN ({q})""", tuple(ids)):
                out.append({"rel": "mineral_site_location",
                            "other": r["loc"] if _fwd else r["subj"],
                            "confidence": r["conf"], "tier": r["tier"] or 1,
                            "ev": "direct", "source_type": "government_open_data",
                            "source_url": None, "via": r["subj"] if _fwd else r["loc"],
                            "forward": _fwd, "label_forward": _fwd,
                            "edge_id": ("mev:" if _fwd else "mevr:") + r["event_id"],
                            "verification": "hardened", "date": r["ts"],
                            "precision": r["prec"], "contradicting": "[]",
                            "human_status": "unverified"})

        # the place hierarchy, both directions
        for r in self.con.execute(f"""
            SELECT entity_id, parent_location_id p FROM locations
             WHERE entity_id IN ({q}) AND parent_location_id IS NOT NULL""", tuple(ids)):
            out.append({"rel": "within", "other": r["p"],
                        "confidence": DERIVED_CONF["within"], "tier": 1,
                        "ev": "direct", "source_type": "census", "source_url": None,
                        "via": r["entity_id"], "edge_id": "loc:" + r["entity_id"],
                        "verification": "hardened", "contradicting": "[]",
                        "human_status": "unverified"})
        for r in self.con.execute(f"""
            SELECT entity_id, parent_location_id p FROM locations
             WHERE parent_location_id IN ({q})""", tuple(ids)):
            out.append({"rel": "within", "other": r["entity_id"],
                        "confidence": DERIVED_CONF["within"], "tier": 1, "ev": "direct", "source_type": "census",
                        "source_url": None, "via": r["p"],
                        "edge_id": "locr:" + r["entity_id"], "verification": "hardened",
                        "contradicting": "[]", "human_status": "unverified"})

        # a group member is never its own neighbour
        idset = set(ids)
        return [e for e in out if e["other"] not in idset]

    # ══════════════════════════════════════════════════════════════════════════
    # THE BOUNDED PATH — what a request actually runs
    # ══════════════════════════════════════════════════════════════════════════
    # 🔴 WHY THIS EXISTS.  `_raw_edges()` above materialises EVERY relation before
    # `k` is applied.  Measured against Scope's own app with the map mounted in it,
    # 8 concurrent clients rotating the 40 highest-degree entities dragged the
    # HOST's front page from 2.4 ms to 65.7 ms — 27x — while completing 18 map
    # requests in three seconds.  The `k` cap never touched it: k=1 cost 128.5 ms
    # and k=200 cost 124.7 ms.
    #
    # ⚠️ AFTER THIS CHANGE THE HOST DEGRADES 6.3x AND THROUGHPUT STILL FALLS WITH
    # CONCURRENCY (0.74x from one client to eight, against 0.22x before).  The
    # residual is `_census`, which is O(degree) and cannot be otherwise.  This is
    # an improvement, not a resolution, and the deploy gate it was written for
    # remains FAILED on that second limb — see
    # SESSION-2026-09-02-map-alpha-bounded-fetch-fix.
    #
    # The replacement computes the DISCLOSED COUNTS by database aggregate and
    # fetches only the rows it will actually return.
    #
    # ⚠️ EQUIVALENCE IS THE WHOLE POINT AND IT IS PROVEN, NOT ARGUED.  Round-robin
    # draws at most `k` rows in total and therefore at most `k` from any one
    # bucket, and each bucket is ordered by (-rank, other) — so only the top `k` of
    # a bucket can ever be drawn, and fetching exactly that is not an
    # approximation.  `_raw_edges()` is KEPT as the reference implementation and a
    # test compares the two across the fixture and a live sample.

    # 🔴 DERIVED, NOT LISTED.  The first version hardcoded the `edges`
    # relationship types.  `edges` has 34 of them in its CHECK and the list will
    # drift the first time one is added — and a rel the census counts but
    # `_bounded` cannot serve produces an EMPTY bucket, which silently returns
    # fewer neighbours than `k` with nothing saying so.  Everything that is not one
    # of the four derived relations comes from `edges`, by construction.
    _DERIVED_RELS = frozenset(
        ("place_of_performance", "patent_address", "within", "mineral_site_location"))

    @staticmethod
    def _rank(e) -> float:
        """Ordering only.  A missing confidence is NOT a zero and never reaches the
        payload as one; it has to sort somewhere and last is the honest place."""
        return e["confidence"] if e["confidence"] is not None else -1.0

    @classmethod
    def _better(cls, a, b) -> bool:
        """Is `a` the row to keep for this (other, rel)?

        🔴 THE TIE-BREAK IS EXPLICIT, AND MAKING IT SO IS A REAL BEHAVIOUR CHANGE.
        The old code kept the first row it happened to see at the maximum rank —
        which is SQLite's scan order, an implementation detail.  Adding a covering
        index changes scan order, so "keep whatever came first" was never stable
        under the very change this work order makes.  Smallest `edge_id` wins
        instead: deterministic, index-independent, and identical for the >99% of
        keys that have exactly one row.
        """
        ra, rb = cls._rank(a), cls._rank(b)
        if ra != rb:
            return ra > rb
        return a["edge_id"] < b["edge_id"]

    # ── the precomputed census, and the seal that says it belongs here ───────
    def _census_table_is_trustworthy(self) -> bool:
        """Is `entity_census` a description of THIS database, or of some other one?

        🔴 PRESENCE OF THE TABLE IS NOT EVIDENCE THAT IT IS CORRECT, and the
        numbers it carries are the ones the UI DISCLOSES.  `build_census()` seals
        it with the row counts of every table the census is derived from; this
        re-counts them and refuses the fast path on any disagreement.

        ⚠️ WHAT THIS DOES NOT PROVE, said plainly: row counts catch a truncated
        table, a snapshot rebuilt without its census, and a census copied from a
        different-sized database.  They do NOT catch an in-place UPDATE that
        leaves every count unchanged.  The structural guarantee against that is
        that the snapshot is built as one artifact and replaced as a whole file —
        this seal is the cheap check that the artifact is still intact, not a
        cryptographic one, and it is not offered as one.
        """
        if os.environ.get("OSINT_MAP_LIVE_CENSUS"):
            # ⚠️ THE SWITCH THAT MAKES THE BEFORE/AFTER MEASUREMENT A CONTROLLED
            # ONE, and an operational escape hatch besides.  The load-test gate
            # compares a request that reads this table against the same request
            # computing the same numbers live; doing that across two builds would
            # vary the code AND the data at once, so instead one snapshot is
            # served both ways and only this flag moves.  If the table is ever
            # suspected in production it is also how you turn it off without
            # rebuilding anything — slower, identical answers.
            return False
        try:
            seal = self.con.execute(
                "SELECT rows, source_counts FROM entity_census_seal").fetchone()
        except sqlite3.OperationalError:
            return False                      # no table — a live DB, compute live
        if seal is None:
            return False
        want = json.loads(seal["source_counts"])
        # ⚠️ THE SEAL IS DATA INSIDE THE DATABASE, so its keys are not trusted as
        # SQL.  They are matched against the tuple the builder derives from, and
        # anything else refuses the fast path rather than being interpolated into
        # a statement.  Writing the seal already requires write access to the
        # snapshot — this is not the last line of defence — but a name read from a
        # file has no business reaching a query unchecked.
        if set(want) != set(CENSUS_SOURCES):
            print(f"⚠️  entity_census seal names {sorted(want)}, not "
                  f"{sorted(CENSUS_SOURCES)} — computing the census live", file=sys.stderr)
            return False
        for t, n in want.items():
            got = self.con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            if got != n:
                print(f"⚠️  entity_census seal rejects {t}: {got} rows, sealed at {n}"
                      f" — computing the census live", file=sys.stderr)
                return False
        got = self.con.execute("SELECT COUNT(*) FROM entity_census").fetchone()[0]
        if got != seal["rows"]:
            print(f"⚠️  entity_census holds {got} rows, sealed at {seal['rows']}"
                  f" — computing the census live", file=sys.stderr)
            return False
        return True

    def _census(self, ids: list[str]) -> tuple[int, dict[str, int], int]:
        """`degree`, per-relation distinct neighbours, and total distinct
        neighbours — READ, not computed, when the database carries the table.

        🔴 THIS IS THE WHOLE OF THE SECOND PERFORMANCE PASS.  The live version
        below is O(degree) by nature — you cannot count 26,179 distinct
        neighbours without touching all 26,179 — and re-measured on the serving
        snapshot it was **88.5% of a request's time** (37.1 ms of 38.7 ms for
        `Apple Inc.`).  Nothing about the shape of that query can be made cheap,
        so it is not asked at request time at all: `build_census()` computes it
        ONCE for every canonical entity when the serving snapshot is built, and a
        request does a single primary-key seek into a `WITHOUT ROWID` table.

        ⚠️ WHY THIS INTRODUCES NO STALENESS.  The snapshot is already a static
        artifact replaced as a whole file (End State #8 — the map is not
        live-synced), and this table is written inside the same build, from the
        same rows, in the same transaction.  It cannot describe a database other
        than the one it is in.  Against a LIVE database — a dev box reading
        `osint.db`, which other sessions write — there is no such table and this
        method computes, exactly as before.  The fast path is only taken where
        its assumption is structurally true.

        🔴 A MISSING ROW FALLS BACK, IT DOES NOT GUESS.  `degree` and
        `rel_census` are DISCLOSED numbers; a cap that also shrank the count it
        reports would be indistinguishable from a small graph, which is the
        defect the disclosure exists to prevent.  So an id the table does not
        cover is computed live — the right answer slowly — and never defaulted
        to zero.  `_census_misses` counts them, so a fallback is visible rather than
        silent.  WARNING: nothing in the build asserts that counter is zero.  The
        suite checks it against the fixture, and the session that added this
        checked it once across all 235,987 canonical entities -- an observation
        that has been made, not an invariant the builder enforces.
        """
        if self._census_tbl:
            r = self.con.execute(
                "SELECT degree, distinct_neighbors, rel_census FROM entity_census"
                " WHERE canonical_entity_id = ?", (ids[0],)).fetchone()
            if r is not None:
                return r[0], json.loads(r[2]), r[1]
            with self._lock:
                self._census_misses += 1
        return self._census_live(ids)

    def _census_live(self, ids: list[str]) -> tuple[int, dict[str, int], int]:
        """`degree`, per-relation distinct neighbours, and total distinct
        neighbours — from aggregates, never from a materialised set.

        These are the numbers the UI DISCLOSES, so they must stay TRUE while the
        rows are bounded.  A cap that also shrank the count it reports would be
        indistinguishable from a small graph, which is the defect the disclosure
        exists to prevent.

        ⚠️ THIS IS NO LONGER THE COST OF A REQUEST — IT IS THE GROUND TRUTH AND
        THE FALLBACK.  It is O(degree) by nature: you cannot count 26,179 distinct
        neighbours without touching all 26,179.  Re-measured on the serving
        snapshot it is 37.1 ms of `Apple Inc.`'s 38.7 ms request, 88.5% of the
        aggregate across the ten highest-degree entities.  `build_census()`
        precomputes exactly this, once, at snapshot-build time, and `_census()`
        above reads the result.  This method stays because the precomputed table
        has to be provable equal to SOMETHING, and because a database without the
        table — the live `osint.db` a dev box reads — still has to be served
        correctly.

        ⚠️ IT IS SQL RATHER THAN PYTHON ON PURPOSE, and that stops mattering here.
        `sqlite3` releases the GIL while stepping, so this parallelised better
        than the dict-building it replaced — 89 req/s at eight concurrent clients
        against 11 — but not enough: throughput per client still fell as clients
        were added (0.60x from 1 to 8, against 0.22x before).

        🔴 AND PRECOMPUTATION DID NOT FIX THAT EITHER — MEASURED, NOT ASSUMED.
        Removing this query from the request took it to 1.41 ms and took throughput
        at one client from 118 to 345 req/s, and BOTH limbs of the deploy gate got
        WORSE: the host page under eight hostile clients went 6.8x -> 22.8x and
        under thirty 13.9x -> 137x, and the throughput RATIO from one to thirty
        clients 0.73x -> 0.22x.

        ⚠️ AND A CLAIM THAT WAS HERE AND WAS BACKWARDS: I wrote that this build
        "serves 46% LESS map traffic under thirty clients".  It serves MORE — 45 ->
        251 req/s at eight clients and 70 -> 166 at thirty, measured with the load
        generator split across six processes.  The single-process generator the
        harness uses has its own GIL, so once the server got several times faster
        the CLIENT became the bottleneck and a client-side rate read the fast build
        as doing less work.  Four of the session's own artefacts already showed the
        opposite and I did not read them; an independent verifier did.  The
        throughput LIMB still fails — the ratio falls in both arms — but it fails
        with absolute throughput several times higher, which is a different fact.  A hostile caller is
        a closed loop — cheaper requests mean more of them — and nothing inside one
        process separates the map's CPU from its host's.  Do not read this method's
        cost as the thing standing between the map and a deploy.

        🔴 A PER-SOURCE SPLIT WAS TRIED AND WAS MEASURABLY WORSE, WHICH IS WHY THIS
        SHAPE IS STILL HERE.  Five small `UNION ALL` tallies plus a global `UNION`
        looked obviously cheaper in isolation (6.5 ms + 9.4 ms against 29 ms) and
        came out at 47.6 ms in place, taking a whole request from 39 ms to 50 ms.
        The isolated numbers were measured with `= ?` and no exclusion; the real
        thing needs `IN (...)` over a canonical group and `NOT IN (...)` to drop
        self-edges, and those put the cost back. Recorded so the next person does
        not spend the same afternoon.
        """
        q = ",".join("?" * len(ids))
        pairs = f"""
            SELECT relationship_type rel, entity_b_id o FROM edges WHERE entity_a_id IN ({q})
      UNION ALL SELECT relationship_type, entity_a_id FROM edges WHERE entity_b_id IN ({q})
      UNION ALL SELECT 'place_of_performance', ev.location_entity_id FROM events ev
                 WHERE ev.event_type='government_contract_awarded'
                   AND ev.location_entity_id IS NOT NULL AND ev.subject_entity_id IN ({q})
      UNION ALL SELECT 'place_of_performance', ev.subject_entity_id FROM events ev
                 WHERE ev.event_type='government_contract_awarded'
                   AND ev.location_entity_id IN ({q})
      UNION ALL SELECT 'mineral_site_location', ev.location_entity_id FROM events ev
                 WHERE ev.event_type IN ('mineral_resource_identified','drilling_activity')
                   AND ev.location_entity_id IS NOT NULL AND ev.subject_entity_id IN ({q})
      UNION ALL SELECT 'mineral_site_location', ev.subject_entity_id FROM events ev
                 WHERE ev.event_type IN ('mineral_resource_identified','drilling_activity')
                   AND ev.location_entity_id IN ({q})
      UNION ALL SELECT 'patent_address', pl.location_entity_id FROM patent_location pl
                 WHERE pl.patent_entity_id IN ({q}) AND pl.location_entity_id IS NOT NULL
      UNION ALL SELECT 'patent_address', pl.patent_entity_id FROM patent_location pl
                 WHERE pl.location_entity_id IN ({q})
      UNION ALL SELECT 'within', parent_location_id FROM locations
                 WHERE entity_id IN ({q}) AND parent_location_id IS NOT NULL
      UNION ALL SELECT 'within', entity_id FROM locations WHERE parent_location_id IN ({q})"""
        # a group member is never its own neighbour — the same exclusion the
        # reference implementation applies, applied BEFORE anything is counted
        holes = f"SELECT rel, o FROM ({pairs}) WHERE o NOT IN ({q})"
        args = tuple(ids) * 11
        degree, per = 0, {}
        for rel, raw, dist in self.con.execute(
                f"SELECT rel, COUNT(*), COUNT(DISTINCT o) FROM ({holes}) GROUP BY rel", args):
            degree += raw
            per[rel] = dist
        total = self.con.execute(
            f"SELECT COUNT(DISTINCT o) FROM ({holes})", args).fetchone()[0]
        return degree, per, total

    def _bounded(self, ids: list[str], rel: str, k: int) -> list[dict]:
        """At most `k` collapsed rows for one relation type, in (-rank, other)
        order — the exact prefix round-robin could draw from this bucket.

        🔴 STREAMED AND STOPPED, NOT `LIMIT`ed.  The first version put
        `LIMIT k + n` on each cursor, which bounds ROWS — and a neighbour can
        appear on several rows (one `patent_location` row per role and sequence, a
        company on several awards).  41 rows collapsed to 35 distinct patents and
        the bucket came up short, so 56 of 540 entities returned fewer neighbours
        than the reference.  Bounding rows is not bounding neighbours.

        ⭐ WHY STOPPING EARLY IS EXACT, not an approximation.  Each cursor is
        ordered by (-rank, other) and they are merged in that order, so the stream
        is globally ordered.  All rows sharing one (rank, other) are therefore
        adjacent, and any row arriving after the (k+1)-th distinct neighbour has a
        strictly greater (-rank, other) — it can only concern a neighbour that
        already sorts past the k-th.  So the moment a (k+1)-th distinct neighbour
        appears, the first k are final.
        """
        idset = set(ids)
        best: dict[str, dict] = {}

        def offer(row) -> bool:
            """True once the k+1-th distinct neighbour has been seen."""
            o = row["other"]
            if o in idset:
                return False
            cur = best.get(o)
            if cur is None or self._better(row, cur):
                best[o] = row
            return len(best) > k

        def drain(stream, ordered: bool):
            """🔴 THE EARLY STOP IS ONLY VALID ON AN ORDERED STREAM, and the first
            version applied it to an unordered one.  Stopping at the (k+1)-th
            distinct neighbour is exact when the stream arrives in (-rank, other)
            order — every later row concerns a neighbour that already sorts past
            the k-th.  Applied to rows in database order it truncates by ARRIVAL,
            which is not an order at all.
            ⚠️ Bare county holds 13 equally-confident awardees; at k=5 the bounded
            path returned aw0/aw1/aw2/aw3 where the reference returned
            aw0/aw1/aw10/aw11 — lexicographic, which is the rule.  The live sample
            of 540 entities did NOT catch this: every real `place_of_performance`
            and `mineral_site_location` bucket is smaller than `k`, so the stop
            never fired.  The fixture was built awkward on purpose and it earned
            its keep here."""
            for row in stream:
                stop = offer(row)
                if stop and ordered:
                    return

        if rel not in self._DERIVED_RELS:
            # ⚠️ ONE CURSOR PER (GROUP MEMBER, DIRECTION).  An `IN (...)` cannot be
            # walked in confidence order from the index — SQLite would sort every
            # matching row first, which is precisely the O(degree) cost being
            # removed.  Separate cursors each stream their own index range and the
            # merge puts them back in order lazily.
            cursors = []
            for e in ids:
                cursors.append(self._edge_stream(e, rel, True))
                cursors.append(self._edge_stream(e, rel, False))
            drain(heapq.merge(*cursors, key=lambda r: (-self._rank(r), r["other"])), True)
        elif rel == "patent_address":
            # ⚠️ RANK IS A CONSTANT HERE — every `patent_address` row carries
            # DERIVED_CONF — so (-rank, other) collapses to `other`, and the index
            # order IS the required order.
            cursors = []
            for e in ids:
                cursors.append(self._patloc_stream(e, True))
                cursors.append(self._patloc_stream(e, False))
            drain(heapq.merge(*cursors, key=lambda r: r["other"]), True)
        elif rel == "within":
            cursors = []
            for e in ids:
                cursors.append(self._within_stream(e, True))
                cursors.append(self._within_stream(e, False))
            drain(heapq.merge(*cursors, key=lambda r: r["other"]), True)
        else:
            # the event-derived relations.  After the serving snapshot's prune the
            # whole `events` table is 440 rows, so bounding these would cost more
            # than it saves.
            # unordered — the whole `events` table is 440 rows after the
            # snapshot prune, so draining it fully is cheaper than ordering it
            drain(iter(self._event_rows(ids, rel)), False)

        rows = sorted(best.values(), key=lambda e: (-self._rank(e), e["other"]))
        return rows[:k]

    # ── the streams the merge consumes ───────────────────────────────────────
    # Each yields already-built payload rows in the bucket's own order, lazily, so
    # a cursor that is never exhausted costs only the rows actually pulled.

    def _edge_stream(self, eid: str, rel: str, forward: bool):
        col, other_col = ("entity_a_id", "entity_b_id") if forward else ("entity_b_id", "entity_a_id")
        cur = self.con.execute(f"""
            SELECT e.edge_id, e.entity_a_id a, e.entity_b_id b,
                   e.confidence_score conf, e.source_reliability_tier tier,
                   e.direct_or_inferred ev, e.source_type, e.source_url,
                   e.contradicting_sources contra, e.human_verification_status hvs,
                   ea.entity_type a_type
              FROM edges e JOIN entities ea ON ea.entity_id = e.entity_a_id
             WHERE e.{col} = ? AND e.relationship_type = ?
             ORDER BY e.confidence_score DESC, e.{other_col}""", (eid, rel))
        for r in cur:
            mine = r["a"] if forward else r["b"]
            other = r["b"] if forward else r["a"]
            label_forward = forward
            # the same fix on the streaming path — a one-sided repair here would be
            # the "rule enforced in one of the two places it applies" failure
            if rel in ("ownership", "contractor") and r["a_type"] in ("asset", "permit"):
                label_forward = not forward
            yield {"rel": rel, "other": other, "confidence": r["conf"],
                   "tier": r["tier"], "ev": r["ev"], "source_type": r["source_type"],
                   "source_url": r["source_url"], "via": mine,
                   "forward": forward, "label_forward": label_forward,
                   "edge_id": r["edge_id"], "verification": "hardened",
                   "contradicting": r["contra"], "human_status": r["hvs"]}

    def _patloc_stream(self, eid: str, forward: bool):
        if forward:
            cur = self.con.execute("""
                SELECT pl.patent_location_id, pl.patent_entity_id pid,
                       pl.location_entity_id loc, pl.role, pl.geospatial_precision prec
                  FROM patent_location pl
                 WHERE pl.patent_entity_id = ? AND pl.location_entity_id IS NOT NULL
                 ORDER BY pl.location_entity_id""", (eid,))
        else:
            cur = self.con.execute("""
                SELECT pl.patent_location_id, pl.patent_entity_id pid,
                       pl.location_entity_id loc, pl.role, pl.geospatial_precision prec
                  FROM patent_location pl WHERE pl.location_entity_id = ?
                 ORDER BY pl.patent_entity_id""", (eid,))
        for r in cur:
            yield self._patloc_row(r, forward)

    def _within_stream(self, eid: str, forward: bool):
        if forward:
            cur = self.con.execute(
                "SELECT entity_id, parent_location_id p FROM locations "
                "WHERE entity_id=? AND parent_location_id IS NOT NULL", (eid,))
            for r in cur:
                yield self._within_row(r["entity_id"], r["p"], True)
        else:
            cur = self.con.execute(
                "SELECT entity_id, parent_location_id p FROM locations "
                "WHERE parent_location_id=? ORDER BY entity_id", (eid,))
            for r in cur:
                yield self._within_row(r["entity_id"], r["p"], False)

    def _patloc_row(self, r, forward: bool) -> dict:
        return {"rel": "patent_address",
                "other": r["loc"] if forward else r["pid"],
                "confidence": DERIVED_CONF["patent_address"], "tier": 1,
                "ev": "direct", "source_type": "patentsview", "source_url": None,
                "via": r["pid"] if forward else r["loc"],
                "edge_id": ("pl:" if forward else "plr:") + r["patent_location_id"],
                "verification": SOURCES["patent"].verification,
                "role": r["role"], "precision": r["prec"], "contradicting": "[]",
                "human_status": "unverified", "forward": forward,
                "label_forward": forward}

    def _within_row(self, child: str, parent: str, forward: bool) -> dict:
        return {"rel": "within", "other": parent if forward else child,
                "confidence": DERIVED_CONF["within"], "tier": 1, "ev": "direct",
                "source_type": "census", "source_url": None,
                "via": child if forward else parent,
                "edge_id": ("loc:" + child) if forward else ("locr:" + child),
                "verification": "hardened", "contradicting": "[]",
                "human_status": "unverified", "forward": forward,
                "label_forward": forward}

    def _event_rows(self, ids: list[str], rel: str) -> list[dict]:
        q = ",".join("?" * len(ids))
        out = []
        if rel == "place_of_performance":
            for fwd, where in ((True, "ev.subject_entity_id"), (False, "ev.location_entity_id")):
                extra = "AND ev.location_entity_id IS NOT NULL" if fwd else ""
                for r in self.con.execute(f"""
                    SELECT ev.event_id, ev.subject_entity_id subj, ev.location_entity_id loc,
                           ev.event_timestamp ts, ev.geospatial_precision prec,
                           (SELECT MIN(e.confidence_score) FROM edges e
                             WHERE e.event_id=ev.event_id
                               AND e.relationship_type='government_contract') conf
                      FROM events ev
                     WHERE ev.event_type='government_contract_awarded' {extra}
                       AND {where} IN ({q})""", tuple(ids)):
                    out.append({"rel": "place_of_performance",
                                "other": r["loc"] if fwd else r["subj"],
                                "confidence": r["conf"] if r["conf"] is not None else 0.6,
                                "tier": 1, "ev": "direct", "source_type": "usaspending",
                                "source_url": None,
                                "via": r["subj"] if fwd else r["loc"],
                                "edge_id": ("ev:" if fwd else "evr:") + r["event_id"],
                                "verification": "hardened", "date": r["ts"],
                                "precision": r["prec"], "contradicting": "[]",
                                "human_status": "unverified", "forward": fwd,
                                "label_forward": fwd})
        elif rel == "mineral_site_location":
            for fwd, where in ((True, "ev.subject_entity_id"), (False, "ev.location_entity_id")):
                for r in self.con.execute(f"""
                    SELECT ev.event_id, ev.subject_entity_id subj, ev.location_entity_id loc,
                           ev.event_timestamp ts, ev.geospatial_precision prec,
                           (SELECT MIN(s.confidence_score) FROM site_commodity_assertion s
                             WHERE s.site_entity_id = ev.subject_entity_id) conf,
                           (SELECT MIN(s.source_reliability_tier) FROM site_commodity_assertion s
                             WHERE s.site_entity_id = ev.subject_entity_id) tier
                      FROM events ev
                     WHERE ev.event_type IN ('mineral_resource_identified','drilling_activity')
                       AND ev.location_entity_id IS NOT NULL
                       AND {where} IN ({q})""", tuple(ids)):
                    out.append({"rel": "mineral_site_location",
                                "other": r["loc"] if fwd else r["subj"],
                                "confidence": r["conf"], "tier": r["tier"] or 1,
                                "ev": "direct", "source_type": "government_open_data",
                                "source_url": None,
                                "via": r["subj"] if fwd else r["loc"],
                                "edge_id": ("mev:" if fwd else "mevr:") + r["event_id"],
                                "verification": "hardened", "date": r["ts"],
                                "precision": r["prec"], "contradicting": "[]",
                                "human_status": "unverified", "forward": fwd,
                                "label_forward": fwd})
        return out

    def neighborhood_reference(self, eid: str, k: int = MAX_K) -> dict | None:
        """THE REFERENCE IMPLEMENTATION — materialise everything, then slice.

        🔴 NOT THE REQUEST PATH, AND KEPT ON PURPOSE.  This is what
        `neighborhood()` used to do, and it is retained so the bounded version can
        be proven equal to it rather than argued equal to it — forever, by a test,
        not once in a session log.  It is O(degree) by construction, which is
        exactly why it is not what a request runs.

        Any divergence between this and `neighborhood()` is a defect in the
        bounded path, and the test that compares them is the only thing standing
        between "we think the fast path is equivalent" and knowing it.
        """
        cid = self.canonical(eid)
        n = self.node(cid)
        if n is None:
            return None
        k = max(1, min(MAX_K, int(k)))
        ids = self.group(cid)
        raw = self._raw_edges(ids)
        degree = len(raw)
        best: OrderedDict[tuple, dict] = OrderedDict()
        for e in raw:
            key = (e["other"], e["rel"])
            if key not in best or self._better(e, best[key]):
                best[key] = e
        buckets = defaultdict(list)
        for e in best.values():
            buckets[e["rel"]].append(e)
        for v in buckets.values():
            v.sort(key=lambda e: (-self._rank(e), e["other"]))
        rel_census = {r: len(v) for r, v in buckets.items()}
        order = sorted(buckets, key=lambda r: (-len(buckets[r]), r))
        chosen, i = [], 0
        while len(chosen) < k and any(buckets[r] for r in order):
            r = order[i % len(order)]
            if buckets[r]:
                chosen.append(buckets[r].pop(0))
            i += 1
        neighbors, nodes = [], {}
        for e in chosen:
            on = self.node(self.canonical(e["other"]))
            if on is None:
                continue
            nodes[on["uid"]] = on
            neighbors.append({
                "uid": on["uid"], "rel": e["rel"],
                "rel_label": (REL_LABEL.get(e["rel"], e["rel"])
                              if e.get("label_forward", e.get("forward", True))
                              else REL_LABEL_REVERSE.get(
                                  e["rel"], REL_LABEL.get(e["rel"], e["rel"]))),
                "confidence": e["confidence"], "tier": e["tier"], "ev": e["ev"],
                "source_type": e["source_type"], "verification": e["verification"],
                "via": None if e["via"] == cid else e["via"],
                "role": e.get("role"), "date": e.get("date"),
            })
        return {
            "node": n, "degree": degree,
            "distinct_neighbors": len({e["other"] for e in best.values()}),
            "returned": len(neighbors), "cap": k,
            "capped": len(best) > len(chosen),
            "neighbors": neighbors, "nodes": list(nodes.values()),
            "rel_census": dict(sorted(rel_census.items())),
        }

    def neighborhood(self, eid: str, k: int = MAX_K) -> dict | None:
        """The node, its true degree, and at most `k` neighbours.

        `k` is clamped to `MAX_K` here rather than trusted from the caller — read
        that constant's note for what the clamp does and does not bound."""
        k = max(1, min(MAX_K, int(k)))
        if self._cache is not None:
            # the cache is shared across threads; the OrderedDict is not
            with self._lock:
                hit = self._cache.get((eid, k))
                if hit is not None:
                    self._cache.move_to_end((eid, k))
            if hit is not None:
                return hit
        cid = self.canonical(eid)
        n = self.node(cid)
        if n is None:
            return None
        ids = self.group(cid)

        # 🔴 THE DISCLOSED COUNTS ARE READ, THE ROWS COME FROM A BOUNDED FETCH.
        # `degree`, `distinct_neighbors` and `rel_census` stay TRUE while the work
        # stops scaling with them — first by moving from a materialised set to an
        # aggregate, and now by moving that aggregate out of the request entirely.
        # ⚠️ THIS DID NOT CLOSE THE DEPLOY GATE AND THE COMMENT WILL NOT PRETEND IT
        # DID.  A request went from 38.7 ms to 1.41 ms and the host page under
        # eight hostile clients got WORSE, 6.8x to 22.8x, and under thirty 13.9x to
        # 137x, while the map itself served 2.4-5.6x MORE traffic.  Per-request cost
        # was never what the gate measured.
        # ⚠️ WHY, EXACTLY, IS NOT ESTABLISHED.  A GIL/saturation story was proposed
        # and an independent verifier disproved it: server CPU peaks at 283% of a
        # 1000% box, a pure-Python thread inside the process is not starved, and a
        # 250x sweep of sys.setswitchinterval moves nothing.  What it did find is
        # that the damage is NOT uniform across routes -- static routes degrade ~5x
        # while Scope's `/`, the one that opens a SQLite connection per request,
        # degrades 71x.  That points at contention in SQLite or the page cache, not
        # at CPU.  Unresolved, and named as unresolved.
        # See SESSION-2026-09-02-map-alpha-performance-redesign.
        degree, rel_census, distinct = self._census(ids)

        # ⚠️ THE ROUND-ROBIN ORDER IS COMPUTED FROM THE TRUE PER-TYPE COUNTS, NOT
        # FROM THE FETCHED BUCKETS.  Ordering by fetched size would clamp every
        # large type to `k` and re-order them by name — silently changing which
        # neighbours a capped expansion returns.  The census is what keeps the
        # selection identical to the reference implementation.
        order = sorted(rel_census, key=lambda r: (-rel_census[r], r))
        buckets = {r: self._bounded(ids, r, k) for r in order}

        chosen = []
        i = 0
        while len(chosen) < k and any(buckets[r] for r in order):
            r = order[i % len(order)]
            if buckets[r]:
                chosen.append(buckets[r].pop(0))
            i += 1

        neighbors, nodes = [], {}
        for e in chosen:
            on = self.node(self.canonical(e["other"]))
            if on is None:
                continue
            nodes[on["uid"]] = on
            neighbors.append({
                "uid": on["uid"], "rel": e["rel"],
                "rel_label": (REL_LABEL.get(e["rel"], e["rel"])
                              if e.get("label_forward", e.get("forward", True))
                              else REL_LABEL_REVERSE.get(
                                  e["rel"], REL_LABEL.get(e["rel"], e["rel"]))),
                "confidence": e["confidence"], "tier": e["tier"], "ev": e["ev"],
                "source_type": e["source_type"], "verification": e["verification"],
                "via": None if e["via"] == cid else e["via"],
                "role": e.get("role"), "date": e.get("date"),
            })

        out = {
            "node": n,
            "degree": degree,
            "distinct_neighbors": distinct,
            "returned": len(neighbors),
            "cap": k,
            "capped": sum(rel_census.values()) > len(chosen),
            "neighbors": neighbors,
            "nodes": list(nodes.values()),
            "rel_census": dict(sorted(rel_census.items())),
        }
        if self._cache is not None:
            with self._lock:
                self._cache[(eid, k)] = out
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
        return out


# ── the precomputed census, built once, read forever ─────────────────────────
# 🔴 THIS QUERY AND `GraphAPI._census_live` ARE ONE RULE WRITTEN TWICE, AND THAT
# IS WHY THEY LIVE IN THE SAME FILE.  Every asymmetry in the live version is
# deliberate and load-bearing, and the precomputed one has to reproduce all of
# them or a DISCLOSED count silently changes:
#
#   * `edges` is walked in BOTH directions, with no NULL guard on either end.
#     A NULL `other` survives the UNION and is then dropped by `o NOT IN (ids)`
#     evaluating to NULL — so NULL neighbours are excluded, and `o IS NOT NULL`
#     below is that same exclusion said out loud.
#   * the FORWARD event and `patent_location` clauses require a non-NULL location;
#     the REVERSE ones get it for free from `location_entity_id IN (ids)`.  The
#     `IS NOT NULL` added to the reverse clauses here is equivalent, not extra:
#     a NULL member can never be in a group.
#   * a group member is never its own neighbour.  Live, that is `o NOT IN (ids)`
#     where `ids` is the canonical group; here it is `canonical(o) = canonical(m)`,
#     which is the same set because `entity_canonical` holds no self-maps and no
#     chains — asserted below rather than assumed, because if either ever appears
#     the two definitions stop agreeing.
CENSUS_PAIRS_SQL = """
          SELECT relationship_type   rel, entity_a_id        m, entity_b_id        o FROM edges
UNION ALL SELECT relationship_type,       entity_b_id,          entity_a_id          FROM edges
UNION ALL SELECT 'place_of_performance',  subject_entity_id,    location_entity_id
            FROM events WHERE event_type = 'government_contract_awarded'
                          AND location_entity_id IS NOT NULL
UNION ALL SELECT 'place_of_performance',  location_entity_id,   subject_entity_id
            FROM events WHERE event_type = 'government_contract_awarded'
                          AND location_entity_id IS NOT NULL
-- 🔴 THE SAME TWO EVENT TYPES AS THE LIVE PATH, OR THE PRECOMPUTED CENSUS AND THE
-- ROWS IT COUNTS DISAGREE.  This aggregate and `_relations()` are one rule written
-- twice, and widening only one of them is exactly the divergence
-- `test_THE_PRECOMPUTED_CENSUS_EQUALS_THE_LIVE_ONE_FOR_EVERY_ENTITY` exists to
-- catch — it caught this, reporting McKenzie County as degree 1 against a live 73.
UNION ALL SELECT 'mineral_site_location', subject_entity_id,    location_entity_id
            FROM events WHERE event_type IN ('mineral_resource_identified','drilling_activity')
                          AND location_entity_id IS NOT NULL
UNION ALL SELECT 'mineral_site_location', location_entity_id,   subject_entity_id
            FROM events WHERE event_type IN ('mineral_resource_identified','drilling_activity')
                          AND location_entity_id IS NOT NULL
UNION ALL SELECT 'patent_address',        patent_entity_id,     location_entity_id
            FROM patent_location WHERE location_entity_id IS NOT NULL
UNION ALL SELECT 'patent_address',        location_entity_id,   patent_entity_id
            FROM patent_location WHERE location_entity_id IS NOT NULL
UNION ALL SELECT 'within',                entity_id,            parent_location_id
            FROM locations WHERE parent_location_id IS NOT NULL
UNION ALL SELECT 'within',                parent_location_id,   entity_id
            FROM locations WHERE parent_location_id IS NOT NULL
"""

# the tables the census is derived from — the seal re-counts exactly these
CENSUS_SOURCES = ("entities", "edges", "entity_canonical", "locations",
                  "patent_location", "events")


def build_census(con: sqlite3.Connection, progress=lambda *_: None) -> dict:
    """Compute `_census_live` for EVERY canonical entity, once, and store it.

    Takes a WRITABLE connection to a serving snapshot (or, in the test suite, to
    a copy of the fixture) and leaves behind `entity_census` and its seal.

    ⭐ ONE PASS, NOT ONE PER ENTITY.  The live census asks "who are THIS group's
    neighbours" and pays O(degree) for the answer; this asks "who is everyone's
    neighbour" once and pays O(total relations) for all 236,420 of them — the
    same rows, read once instead of once per request, and the per-entity cost
    disappears into a GROUP BY.

    🔴 `WITHOUT ROWID`, AND THAT IS THE POINT OF THE WHOLE EXERCISE.  The request
    path does one primary-key seek that returns the payload out of the index
    leaf itself.  A rowid table would be a seek into the index followed by a
    second seek into the table — still fast, but this is the one lookup standing
    between a request and O(degree), and it is worth making it a single b-tree
    descent.
    """
    self_maps = con.execute(
        "SELECT COUNT(*) FROM entity_canonical WHERE entity_id = canonical_entity_id"
    ).fetchone()[0]
    # THE TWO CONDITIONS MUST BE DISJOINT, and written the obvious way they are
    # not.  `canonical_entity_id IN (SELECT entity_id FROM entity_canonical)` is
    # also satisfied by a self-map, so `x -> x` raised on the CHAINED term and the
    # SELF-MAP term was never independently reached: the test that named self-maps
    # passed for the wrong reason, and deleting `self_maps` from the guard below
    # survived the entire suite.  An independent verifier's mutants found it.
    chained = con.execute(
        "SELECT COUNT(*) FROM entity_canonical WHERE entity_id <> canonical_entity_id"
        " AND canonical_entity_id IN (SELECT entity_id FROM entity_canonical"
        "                              WHERE entity_id <> canonical_entity_id)"
    ).fetchone()[0]
    if self_maps or chained:
        # 🔴 NOT A WARNING — A REFUSAL.  `canonical(o) = canonical(m)` is only the
        # same set as `o IN group(m)` while the mapping is flat and irreflexive.
        # With a chain or a self-map the two definitions diverge and the stored
        # counts would disagree with the live ones for exactly the entities
        # nobody would think to check.
        raise ValueError(
            f"entity_canonical is not flat: {self_maps} self-maps, {chained} chained "
            f"— the precomputed census's group exclusion would not match the live one")

    t0 = time.time()
    con.execute("DROP TABLE IF EXISTS entity_census")
    con.execute("DROP TABLE IF EXISTS entity_census_seal")
    con.execute("""CREATE TABLE entity_census (
                       canonical_entity_id TEXT PRIMARY KEY,
                       degree              INTEGER NOT NULL,
                       distinct_neighbors  INTEGER NOT NULL,
                       rel_census          TEXT    NOT NULL
                   ) WITHOUT ROWID""")
    con.execute("""CREATE TABLE entity_census_seal (
                       rows           INTEGER NOT NULL,
                       source_counts  TEXT    NOT NULL,
                       built_at       TEXT    NOT NULL,
                       builder        TEXT    NOT NULL)""")

    # every entity resolved to its canonical id, strays included
    con.execute("""CREATE TEMP TABLE _canon AS
                     SELECT entity_id eid, canonical_entity_id cid FROM entity_canonical
                     UNION ALL
                     SELECT entity_id, entity_id FROM entities
                      WHERE entity_id NOT IN (SELECT entity_id FROM entity_canonical)""")
    con.execute("CREATE UNIQUE INDEX _canon_eid ON _canon(eid)")
    progress("canon", time.time() - t0)

    # every (canonical group, relation, neighbour) the live census would count,
    # with the group's own members already excluded
    con.execute(f"""CREATE TEMP TABLE _pairs AS
                      SELECT c1.cid cid, p.rel rel, p.o o
                        FROM ({CENSUS_PAIRS_SQL}) p
                        JOIN _canon c1 ON c1.eid = p.m
                        LEFT JOIN _canon c2 ON c2.eid = p.o
                       WHERE p.o IS NOT NULL
                         AND IFNULL(c2.cid, p.o) <> c1.cid""")
    n_pairs = con.execute("SELECT COUNT(*) FROM _pairs").fetchone()[0]
    progress("pairs", time.time() - t0, n_pairs)

    per: dict[str, dict[str, int]] = defaultdict(dict)
    degree: dict[str, int] = defaultdict(int)
    for cid, rel, raw, dist in con.execute(
            "SELECT cid, rel, COUNT(*), COUNT(DISTINCT o) FROM _pairs GROUP BY cid, rel"):
        per[cid][rel] = dist
        degree[cid] += raw
    progress("per-rel", time.time() - t0, len(per))
    distinct = dict(con.execute(
        "SELECT cid, COUNT(DISTINCT o) FROM _pairs GROUP BY cid"))
    progress("distinct", time.time() - t0, len(distinct))

    # ⚠️ A ROW FOR EVERY CANONICAL ENTITY, INCLUDING THE UNCONNECTED ONES.  Without
    # them "no row" would mean two different things — an entity with no relations
    # and an entity the build missed — and only one of those is safe to serve.
    def rows():
        for (cid,) in con.execute(
                "SELECT entity_id FROM entities"
                " WHERE entity_id NOT IN (SELECT entity_id FROM entity_canonical)"):
            p = per.get(cid)
            yield (cid, degree.get(cid, 0), distinct.get(cid, 0),
                   json.dumps(p, sort_keys=True, separators=(",", ":")) if p else "{}")

    con.executemany("INSERT INTO entity_census VALUES (?,?,?,?)", rows())
    n_rows = con.execute("SELECT COUNT(*) FROM entity_census").fetchone()[0]
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in CENSUS_SOURCES}
    con.execute("INSERT INTO entity_census_seal VALUES (?,?,?,?)",
                (n_rows, json.dumps(counts),
                 datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 "graph_api.build_census"))
    con.execute("DROP TABLE _pairs")
    con.execute("DROP TABLE _canon")
    con.commit()
    return {"rows": n_rows, "pairs": n_pairs, "source_counts": counts,
            "seconds": round(time.time() - t0, 1)}
