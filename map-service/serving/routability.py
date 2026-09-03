#!/usr/bin/env python3
"""The routability census — every entity type the export ships must be one the
frontend can actually open, or the export does not get written.

🔴 THE FAILURE CLASS THIS EXISTS FOR HAS NOW HAPPENED TWICE, both times silently.
Block 11 shipped 338 `asset` mineral sites whose holders' `degree` counted them
while `neighborhood()` dropped every one (`node() -> None` for a type missing
from `NODE_TYPES`).  The navigability pass found the county picker offering one
seed per dot while `sigBlock` printed 6,213 further rows by name with no way to
open them.  In both cases the data was in the export and the frontend could not
route it, and nothing failed.  A node type missing from the route table is not
"unsupported"; it is a disclosed row that silently cannot be opened.

--- what "routable" means, measured on both ends ---

A picker row opens by `GET /api/graph/<entity_id>`.  That request succeeds only
if `graph_api.node()` knows the entity's RAW `entity_type` — it does
`TYPE_MAP.get(row.entity_type)`, keyed on the database's own value — and the
node then renders only if the frontend's `SYM` table names the VOCABULARY name
that lookup returns (an unknown type falls back to a circle indistinguishable
from `person`, and the legend flags it).  So a raw type is routable only if it
is a KEY of `NODE_TYPES` whose VALUE is a key of `SYM`.  Both halves are read,
neither is assumed — the frontend half is parsed out of the shipped page itself.

🔴 KEYED ON THE RAW TYPE, NOT THE VOCABULARY NAME, AND A VERIFIER IS WHY.  The
first version stamped `NODE_TYPES.get(raw, raw)` and checked the result against
`NODE_TYPES.values()`.  A raw type equal to a VALUE that is not a KEY — `agency`,
whose key is `government_agency` — sailed through as routable while
`graph_api.node()` returned None for it: the Block 11 `asset` shape, re-opened
by the very gate built to close it.  Every row now carries `raw_type` and the
census asks the same question `node()` asks, of the same value.

🔴 AND A ROW WITH A TYPE BUT NO NAME IS NOT ROUTABLE EITHER.  The page's
`entityIndex.add()` refuses to render a bare uid as a name, so a row shipped
with an empty `name` — or a name that IS the id, which is what a lookup
fallback produces — is disclosed by `sigBlock` and silently absent from the
picker.  The same verifier built that row and the census vouched for it.  It
is now counted and refused like an untyped row.

--- the allow-list is an explicit, reasoned exception, never a default ---

`ALLOWLIST` maps a type to the REASON it may ship unroutable.  It is empty.  A
type that lands here must carry a sentence a reader can check; an entry with an
empty reason is refused at import.
"""
from __future__ import annotations

import hashlib
import os
import re
from collections import Counter

# type -> the reason it is allowed to ship without a route.  Empty today.
ALLOWLIST: dict[str, str] = {}
assert all(isinstance(r, str) and r.strip() for r in ALLOWLIST.values()), \
    "an allow-listed type needs a stated reason"


class RoutabilityError(RuntimeError):
    """The frontend route table could not be read.  Loud on purpose: an export
    that cannot see the route table cannot claim anything about routability."""


_SYM = re.compile(r"var\s+SYM\s*=\s*\{(.*?)\};", re.S)
_TAG = re.compile(r"var\s+SRC_TAG\s*=\s*\{(.*?)\};", re.S)
_KEY = re.compile(r"(?:^|[,{\s])([A-Za-z_][A-Za-z0-9_]*)\s*:")


def _keys(body: str) -> list[str]:
    return sorted(set(_KEY.findall(body)))


def route_table(html: str) -> dict:
    """The two tables the frontend actually routes on, parsed from the page.

    `SYM` — node type -> d3 symbol; a type absent here draws as a fallback circle.
    `SRC_TAG` — source id -> the tag a picker row shows; absent = raw id shown.
    Both are `var NAME={ key:value, ... };` literals in `osint_map.html`; if the
    page stops carrying them in that shape this raises rather than returning an
    empty table that would make every type look unroutable — or, worse, routable."""
    m, t = _SYM.search(html), _TAG.search(html)
    if not m or not t:
        raise RoutabilityError(
            "frontend route table not found: expected `var SYM={...};` and "
            "`var SRC_TAG={...};` in the page")
    return {"node_symbols": _keys(m.group(1)), "source_tags": _keys(t.group(1))}


def resolve_frontend(explicit: str | None, here: str) -> str:
    """The page the export will be served with.  Two layouts exist on purpose:
    `map-service/serving/../static/osint_map.html` (the deployed service) and
    `serving/osint-map-v1.html` (the osint-graph repo).  Explicit wins; otherwise
    the first that exists; none is an error, not a silent pass."""
    cands = [explicit] if explicit else [
        os.path.join(here, "..", "static", "osint_map.html"),
        os.path.join(here, "osint-map-v1.html"),
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    raise RoutabilityError(f"no frontend page found at {cands}; pass --frontend")


def entity_refs(obj):
    """Every dict carrying an `entity_id`, at ANY depth.

    🔴 RECURSIVE, NOT A LIST OF KNOWN CONTAINERS.  The navigability pass first
    counted only top-level `detail[]` and missed the `holders[]` nested inside
    commodity rows — the exact shape a hand-written walker keeps missing.  A
    third nesting level added next year is censused here with no code change."""
    if isinstance(obj, dict):
        if "entity_id" in obj:
            yield obj
        for v in obj.values():
            yield from entity_refs(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from entity_refs(v)


def _nameless(r) -> bool:
    n = r.get("name")
    return not isinstance(n, str) or not n.strip() or n == r.get("entity_id")


def referenced(signals) -> dict:
    """What the signals name: RAW entity types (from the stamped `raw_type`),
    sources, rows that carry an entity_id but no `raw_type` — which the census
    cannot vouch for — and rows the page could not render as a picker row
    because they carry no name (or a name that is the id).  Both counts go
    against the export."""
    types: Counter = Counter()
    sources: Counter = Counter()
    untyped = nameless = idless = 0
    for s in signals:
        sources[s.get("source")] += 1
        if s.get("seed"):
            if s.get("seed_raw_type"):
                types[s["seed_raw_type"]] += 1
            else:
                untyped += 1
        for r in entity_refs({k: v for k, v in s.items() if k != "seed"}):
            if not r.get("entity_id"):
                # 🔴 COUNTED, NOT SKIPPED.  The page's guard is `if(!id || !name)
                # return;` — the first remediation closed the `!name` half and a
                # verifier's third survivor walked through the `!id` half one line
                # away: an entity row carrying an empty id is disclosed by
                # `sigBlock` and never offered by the picker, and this loop used to
                # `continue` past it.  It is the same class and gets the same answer.
                idless += 1
                continue
            if r.get("raw_type"):
                types[r["raw_type"]] += 1
            else:
                untyped += 1
            if _nameless(r):
                nameless += 1
    return {"types": dict(types), "sources": dict(sources),
            "untyped_rows": untyped, "nameless_rows": nameless, "idless_rows": idless}


def census(refs: dict, table: dict, graph_map: dict, allowlist: dict | None = None) -> dict:
    """The verdict: which referenced RAW types the page cannot open, and why.

    `graph_map` is `map_sources.NODE_TYPES` — raw entity_type -> vocabulary name —
    passed as the dict, because both halves of the question live in it: is the
    raw type a KEY (graph_api answers), and is its VALUE a symbol the page draws."""
    allow = ALLOWLIST if allowlist is None else allowlist
    frontend = set(table["node_symbols"])
    routable_raw = {raw for raw, vocab in graph_map.items() if vocab in frontend}
    types = refs["types"]                       # keyed on RAW entity_type
    unroutable = sorted(t for t in types if t not in routable_raw and t not in allow)
    return {
        "node_route_table": sorted({graph_map[r] for r in routable_raw}),
        "graph_api_raw_types": sorted(graph_map),
        "frontend_symbols": sorted(frontend),
        "graph_api_types": sorted(set(graph_map.values())),
        "source_tag_table": sorted(table["source_tags"]),
        "types_referenced": dict(sorted(types.items())),
        "sources_referenced": sorted(k for k in refs["sources"] if k),
        "untyped_rows": refs["untyped_rows"],
        "nameless_rows": refs.get("nameless_rows", 0),
        "idless_rows": refs.get("idless_rows", 0),
        "unroutable_types": unroutable,
        "missing_from_frontend": sorted(t for t in unroutable if t in graph_map),
        "missing_from_graph_api": sorted(t for t in unroutable if t not in graph_map),
        "allowlisted": {t: allow[t] for t in sorted(types)
                        if t in allow and t not in routable_raw},
        "unlabelled_sources": sorted(k for k in refs["sources"]
                                     if k and k not in table["source_tags"]),
        "rule": "a row is routable only if its RAW entity_type is a key of "
                "map_sources.NODE_TYPES (what graph_api.node() looks up) whose value is a "
                "key of the shipped page's SYM table, and the row carries a name the "
                "picker can render; anything else must be allow-listed with a reason or "
                "the export is not written",
    }


def refusal(c: dict) -> str | None:
    """Why this export must not be written, or None."""
    why = []
    if c["unroutable_types"]:
        why.append(f"unroutable entity types shipped: {c['unroutable_types']}")
    if c["untyped_rows"]:
        why.append(f"{c['untyped_rows']} entity rows carry no `raw_type` and cannot be censused")
    if c.get("nameless_rows"):
        why.append(f"{c['nameless_rows']} entity rows carry no renderable name — the picker "
                   "would disclose them and never offer them")
    if c.get("idless_rows"):
        why.append(f"{c['idless_rows']} entity rows carry an empty entity_id — the picker "
                   "would disclose them and never offer them")
    return "; ".join(why) or None


def md5_of(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()
