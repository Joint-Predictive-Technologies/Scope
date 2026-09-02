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

import os
import sqlite3
import sys
import threading
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
            if r["rel"] in ("ownership", "contractor") and r["a_type"] == "asset":
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
                 WHERE ev.event_type='mineral_resource_identified'
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
        raw = self._raw_edges(ids)

        # collapse parallel relations onto the same neighbour, keeping the
        # strongest — but count the true degree BEFORE collapsing, because the
        # count the UI shows must be the number of relations that exist
        degree = len(raw)
        # 🔴 A MISSING CONFIDENCE IS NOT A ZERO CONFIDENCE.  A derived
        # `mineral_site_location` row carries the site's own MIN commodity
        # confidence, and a site with no commodity assertion at all would carry
        # None.  None must never reach the payload as 0.0 — that would be this
        # module inventing a number — but it has to ORDER somewhere, and last is
        # the only honest place.  `_rank` is used for sorting only; `confidence`
        # goes out exactly as the graph recorded it, None included.
        _rank = lambda e: e["confidence"] if e["confidence"] is not None else -1.0
        best: OrderedDict[tuple, dict] = OrderedDict()
        for e in raw:
            # 🔴 DIRECTION IS **NOT** IN THIS KEY, AND THAT IS A CORRECTION, NOT
            # THE ORIGINAL.  This work order added it and an independent verifier
            # showed the addition was a regression, after two wrong justifications
            # from me:
            #   1. "a company that is both controller and operator of one site
            #      produces one edge each way" — false. Controller and operator are
            #      different relationship TYPES (`ownership` / `contractor`), so
            #      they never shared a key to begin with.
            #   2. "no pair in this corpus collides across directions" — also
            #      false. `government_contract` is written agency->company by
            #      `scope:rule_11` and company->agency by the usaspending loader,
            #      and FOUR canonical pairs carry both (DoD, DoE and NASA against
            #      LOCKHEED MARTIN CORP; NASA against SPACE EXPLORATION
            #      TECHNOLOGIES CORP).
            # Splitting on direction there rendered TWO rows both reading
            # "contract with Department of Defense", because the split tracks which
            # loader wrote a row rather than anything about the relationship.  The
            # deliberate semantics of this collapse are "parallel relations to one
            # neighbour become one, keeping the strongest" — 8 real Lockheed awards
            # already collapse to one row and are meant to.  Direction belongs in
            # the LABEL, which `label_forward` gives it, and nowhere else.
            key = (e["other"], e["rel"])
            if key not in best or _rank(e) > _rank(best[key]):
                best[key] = e

        buckets = defaultdict(list)
        for e in best.values():
            buckets[e["rel"]].append(e)
        for v in buckets.values():
            v.sort(key=lambda e: (-_rank(e), e["other"]))

        # round-robin so every relationship type present is represented
        chosen, order = [], sorted(buckets, key=lambda r: (-len(buckets[r]), r))
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
            "distinct_neighbors": len({e["other"] for e in best.values()}),
            "returned": len(neighbors),
            "cap": k,
            "capped": len(best) > len(chosen),
            "neighbors": neighbors,
            "nodes": list(nodes.values()),
            "rel_census": {r: len(buckets[r]) + sum(1 for c in chosen if c["rel"] == r)
                           for r in sorted(set(list(buckets) + [c["rel"] for c in chosen]))},
        }
        if self._cache is not None:
            with self._lock:
                self._cache[(eid, k)] = out
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
        return out
