#!/usr/bin/env python3
"""Build the read-only SERVING SNAPSHOT the deployed map reads.

    python serving/build_serving_snapshot.py --out /tmp/osint-map-serving.db

🔴 THE OUTPUT IS NOT `osint.db` AND MUST NEVER BE TREATED AS IT.  It is a
purpose-built artifact containing only what `serving/` reads, and its `events`
table holds **440 of the source's 188,859 rows**.  Anything that queries it for
anything else gets a catastrophic false negative — so it carries a
`_serving_snapshot` table saying exactly that, in the database, where a person
who opens the file will see it.  The filename is deliberately different too.

────────────────────────────────────────────────────────────────────────────────
WHY A SNAPSHOT AT ALL, AND WHY THIS SHAPE

The deployment shares one Railway volume with Scope's own `jpt.db`, and that
volume has ~3.1 GB free.  Three things follow:

  1. SIZE.  `osint.db` is 1,050 MB.  This snapshot is ~624 MB — 473 MB of rows
     plus ~144 MB of the covering indexes below, which are what make the serving
     path's early stop actually stop early — because
     `patent_text` (110 MB), `patent_materiality` (65 MB), `patent_llm_score`
     (51 MB), `patent_text_score` (19 MB), `entity_candidates` (27 MB),
     `filing_item` (3 MB) and their indexes are read by NOTHING in `serving/`.
  2. DATA MINIMISATION.  Those same tables are patent full text and LLM scoring
     output.  A public-facing box has no reason to hold them, and "we shipped the
     whole database because it was easier" is not a reason.
  3. SEPARATION.  `jpt.db` and this file are different files with different
     names and different handles.  Making the served file a purpose-built artifact
     rather than a copy of a working database makes that separation structural
     instead of a promise.

⚠️ WHAT THIS DOES **NOT** DO: it does not prune `entities`, `edges`,
`patent_location` or `locations` by row.  `graph_api` discloses a node's TRUE
degree, and pruning relation rows would make that number quietly wrong — which is
the one thing that surface exists not to do.

Equivalence is not assumed.  `--verify` re-derives it: every export file and a
sample of graph neighbourhoods must be byte-identical between source and snapshot.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# Read by `graph_api` at request time, or by `export_map` when regenerating the
# static planes.  Derived from the code, not from memory — see the module test.
SERVING_TABLES = (
    "entities", "edges", "entity_canonical", "locations", "patent_location",
    "site_commodity_assertion", "commodity", "demand_relevance", "demand_signal",
)
# `events` is kept but PRUNED: these are the only two types either module reads.
EVENT_TYPES = ("government_contract_awarded", "mineral_resource_identified")


def change_counter(path: str) -> int:
    with open(path, "rb") as f:
        return int.from_bytes(f.read(28)[24:28], "big")


def build(src_path: str, out_path: str) -> dict:
    if os.path.abspath(src_path) == os.path.abspath(out_path):
        sys.exit("refusing to write the snapshot over its own source")
    if os.path.exists(out_path):
        os.remove(out_path)

    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    ddl = {r[0]: r[1] for r in src.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL")}
    idx = [(r[0], r[1]) for r in src.execute(
        "SELECT tbl_name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")]
    trg = [(r[0], r[1]) for r in src.execute(
        "SELECT tbl_name, sql FROM sqlite_master WHERE type='trigger' AND sql IS NOT NULL")]
    # what is being LEFT OUT, counted in the source, so the manifest can say so
    omitted = {t: src.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
               for t in sorted(ddl) if t not in SERVING_TABLES + ("events",)}
    src_events = src.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    src.close()

    missing = [t for t in SERVING_TABLES + ("events",) if t not in ddl]
    if missing:
        sys.exit(f"source is missing tables the serving code reads: {missing}")

    t0 = time.time()
    dst = sqlite3.connect(out_path)
    dst.execute("PRAGMA foreign_keys=OFF")
    for t in SERVING_TABLES + ("events",):
        dst.execute(ddl[t])          # verbatim DDL, CHECKs and all
    dst.commit()
    dst.execute(f"ATTACH DATABASE 'file:{src_path}?mode=ro' AS s")
    kept = {}
    for t in SERVING_TABLES:
        dst.execute(f"INSERT INTO main.{t} SELECT * FROM s.{t}")
        kept[t] = dst.execute(f"SELECT COUNT(*) FROM main.{t}").fetchone()[0]
    qmarks = ",".join("?" * len(EVENT_TYPES))
    dst.execute(f"""INSERT INTO main.events SELECT * FROM s.events
                     WHERE event_type IN ({qmarks})
                       AND location_entity_id IS NOT NULL""", EVENT_TYPES)
    kept["events"] = dst.execute("SELECT COUNT(*) FROM main.events").fetchone()[0]
    dst.commit()
    for tbl, sql in idx:
        if tbl in SERVING_TABLES + ("events",):
            dst.execute(sql)
    # 🔴 COVERING INDEXES THE SOURCE DOES NOT HAVE, AND THEY ARE THE POINT OF THE
    # SNAPSHOT BEING A BUILT ARTIFACT RATHER THAN A COPY.  `graph_api` serves a
    # request by walking each relation type's neighbours in (confidence DESC,
    # other) order and STOPPING at `k`.  Without an index in exactly that order
    # SQLite must sort every matching row before returning the first one, so the
    # early stop buys nothing and a 26,000-edge entity costs 26,000 rows either
    # way — which is the defect this whole work order exists to remove.
    #
    # ⚠️ THEY GO HERE AND NOT IN `osint.db`.  The source database is the moat and
    # this is a serving artifact; an index that exists only to make one read path
    # fast belongs with the read path, and adding it upstream would be a write to
    # a database this campaign keeps read-only.
    for sql in (
            # both directions of an edge lookup, ordered the way the walk consumes it
            "CREATE INDEX idx_srv_edges_a ON edges"
            " (entity_a_id, relationship_type, confidence_score DESC, entity_b_id)",
            "CREATE INDEX idx_srv_edges_b ON edges"
            " (entity_b_id, relationship_type, confidence_score DESC, entity_a_id)",
            # patent_address carries a CONSTANT confidence, so its order is `other`
            "CREATE INDEX idx_srv_patloc_fwd ON patent_location"
            " (patent_entity_id, location_entity_id)",
            "CREATE INDEX idx_srv_patloc_rev ON patent_location"
            " (location_entity_id, patent_entity_id)",
    ):
        dst.execute(sql)
    for tbl, sql in trg:
        if tbl in SERVING_TABLES + ("events",):
            dst.execute(sql)
    dst.commit()
    dst.execute("DETACH DATABASE s")

    # 🔴 THE FILE SAYS WHAT IT IS, INSIDE ITSELF.  A README beside it can be lost
    # in a copy; a table cannot.  Anyone who opens this database and runs
    # `SELECT * FROM _serving_snapshot` learns that `events` is 0.2% of the real
    # table before they draw a conclusion from its emptiness.
    manifest = {
        "what": "READ-ONLY SERVING SNAPSHOT — NOT osint.db. Built for the deployed "
                "OSINT map; contains only what serving/ reads.",
        "🔴 events_is_pruned": f"events holds {kept['events']} of {src_events} source rows "
                               f"— ONLY event_type IN {list(EVENT_TYPES)} AND "
                               f"location_entity_id IS NOT NULL. Every other event type "
                               f"(patent_granted, congressional_transaction, "
                               f"insider_transaction, ...) is ABSENT. An empty result "
                               f"here is NOT evidence of absence in the graph.",
        "tables_omitted_entirely": omitted,
        "tables_kept_in_full": {k: v for k, v in kept.items() if k != "events"},
        "source_path": os.path.abspath(src_path),
        "source_bytes": os.path.getsize(src_path),
        "source_change_counter": change_counter(src_path),
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "builder": "serving/build_serving_snapshot.py",
    }
    dst.execute("CREATE TABLE _serving_snapshot (key TEXT PRIMARY KEY, value TEXT)")
    dst.executemany("INSERT INTO _serving_snapshot VALUES (?,?)",
                    [(k, json.dumps(v) if not isinstance(v, str) else v)
                     for k, v in manifest.items()])
    dst.commit()
    dst.execute("VACUUM")
    dst.close()
    manifest["snapshot_bytes"] = os.path.getsize(out_path)
    manifest["build_seconds"] = round(time.time() - t0, 1)
    return manifest


def verify(src_path: str, out_path: str) -> bool:
    """Re-derive equivalence rather than asserting it.

    The whole snapshot decision rests on one claim — that the serving code cannot
    tell this file from `osint.db` — so the claim is checked, not stated."""
    sys.path.insert(0, HERE)
    import export_map, graph_api          # noqa: E402
    import random, tempfile, glob         # noqa: E402

    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        a, b = os.path.join(tmp, "full"), os.path.join(tmp, "snap")
        for db, out in ((src_path, a), (out_path, b)):
            sys.argv = ["x", "--db", db, "--out", out]
            export_map.main()
        n_same = n_diff = 0
        for f in sorted(glob.glob(a + "/**/*.json", recursive=True)):
            rel = os.path.relpath(f, a)
            A, B = json.load(open(f)), json.load(open(os.path.join(b, rel)))
            if rel == "manifest.json":
                for key in ("generated_at", "db"):
                    A.pop(key, None), B.pop(key, None)
            if A == B:
                n_same += 1
            else:
                n_diff += 1
                print(f"  🔴 export differs: {rel}")
        print(f"  export files identical: {n_same}, differing: {n_diff}")
        ok &= n_diff == 0

    full, snap = graph_api.GraphAPI(src_path), graph_api.GraphAPI(out_path)
    ids = [r[0] for r in full.con.execute("SELECT entity_id FROM entities ORDER BY entity_id")]
    random.seed(11)
    sample = random.sample(ids, min(400, len(ids)))
    # the highest-degree entities, which is where a divergence would hide
    sample += [r[0] for r in full.con.execute(
        "SELECT entity_a_id FROM edges GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 12")]
    sample += [r[0] for r in full.con.execute(
        "SELECT entity_id FROM locations WHERE location_type IN ('county','city') LIMIT 60")]
    bad = sum(1 for e in sample if full.neighborhood(e) != snap.neighborhood(e))
    print(f"  neighbourhoods compared: {len(sample)}, differing: {bad}")
    return ok and bad == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("OSINT_DB") or os.path.expanduser(
        "~/dev/osint-graph/data/osint.db"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--verify", action="store_true",
                    help="re-derive export and graph equivalence against the source")
    args = ap.parse_args()

    m = build(args.db, args.out)
    print(json.dumps({k: v for k, v in m.items()
                      if k not in ("🔴 events_is_pruned", "what")}, indent=2))
    print(f"\n  {m['source_bytes']/1e6:.1f} MB  ->  {m['snapshot_bytes']/1e6:.1f} MB "
          f"({100*m['snapshot_bytes']/m['source_bytes']:.0f}%)  in {m['build_seconds']}s")
    print(f"  events: {m['tables_kept_in_full'].get('events', '—')}"
          f"  (pruned — see the _serving_snapshot table)")
    if args.verify:
        print("\n── verifying equivalence ─────────────────────────────────────")
        if not verify(args.db, args.out):
            sys.exit("🔴 SNAPSHOT IS NOT EQUIVALENT — do not deploy it")
        print("  ✅ equivalent")


if __name__ == "__main__":
    main()
