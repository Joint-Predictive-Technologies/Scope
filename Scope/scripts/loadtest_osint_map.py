#!/usr/bin/env python3
"""Measure whether the OSINT map's routes degrade SCOPE'S OWN pages.

    python scripts/loadtest_osint_map.py --port 8123
    python scripts/loadtest_osint_map.py --port 8123 --json out.json

🔴 THIS IS THE GATE, NOT A BENCHMARK.  `/osint-map/` is mounted into Scope's own
FastAPI process, so a slow map query does not cost the map — it costs whoever is
loading Scope's front page at the time.  That is the one risk the shared-domain
deployment shape introduces over a separate service, and the only honest way to
know it is bounded is to make it happen on purpose and watch the host.

⚠️ THE MEASUREMENT IS ON DISK BECAUSE THE NUMBER IS NOT ENOUGH.  A prior session
reported "2.3ms -> 51.1ms" from a throwaway script; the next person cannot check a
number whose producing code is gone, and this campaign has recorded that defect
twice.  Anyone — including an independent verifier — can re-run this and get a
comparable answer.

────────────────────────────────────────────────────────────────────────────────
WHAT IT DOES, AND WHY EACH PART IS THERE

1. BASELINE.  Scope's front page, unloaded.  The number to protect.
2. HOSTILE LOAD.  N concurrent clients expanding the highest-degree entity in the
   graph — the worst case a caller can pick.  Each request carries a DIFFERENT
   forged `X-Forwarded-For`, because `api/rate_limit.py` keys on that header and
   its own docstring says a caller can spread across apparent clients.  Rate
   limiting is a real defence against casual abuse; it is not the thing being
   tested here, and testing "is it safe?" from behind it would be measuring the
   limiter rather than the risk.
3. SCOPE, UNDER THAT LOAD.  The same front page, same sampler.  The delta is the
   finding.
4. THROUGHPUT vs CONCURRENCY.  🔴 The most diagnostic number of the four.  If
   throughput is FLAT as clients are added, the work is serialised — extra clients
   only queue, and the queue is what starves the host.  A fix that lowers latency
   but leaves throughput flat has not fixed the contention.

⚠️ `k` is varied per request to defeat the neighbourhood cache.  A cached run
measures the cache, not the query, and the first request after a deploy is not
cached.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import threading
import time
import urllib.error
import urllib.request
from collections import Counter


def get(base, path, xff=None, timeout=120):
    req = urllib.request.Request(base + path)
    if xff:
        req.add_header("X-Forwarded-For", xff)
    t0 = time.time()
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        r.read()
        return r.status, (time.time() - t0) * 1000, ""
    except urllib.error.HTTPError as e:
        return e.code, (time.time() - t0) * 1000, e.read()[:160].decode("utf-8", "replace")
    except Exception as e:                                   # noqa: BLE001
        return 0, (time.time() - t0) * 1000, str(e)[:160]


def worst_case_entities(db, n=40):
    """The highest-degree entities — checked, not assumed.  A caller picks the
    worst case, so the test does too, and the degree distribution can shift.

    🔴 A POOL, NOT ONE ID, AND THAT IS A CORRECTION.  The first version of the
    throughput curve hammered ONE entity with a repeating `k`, so after the first
    pass every request was a CACHE HIT and the curve read 1,401 req/s at
    concurrency 8 — it was measuring the cache, not the query, and it would have
    declared a serialisation problem solved that had not been touched.  A caller
    trying to hurt the host rotates entities; so does this."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute("""
        SELECT e, COUNT(*) n FROM (SELECT entity_a_id e FROM edges
                                   UNION ALL SELECT entity_b_id FROM edges)
         GROUP BY e ORDER BY n DESC LIMIT ?""", (n,)).fetchall()
    names = {r[0]: r[1] for r in con.execute(
        "SELECT entity_id, display_name FROM entities WHERE entity_id IN (%s)"
        % ",".join("?" * len(rows)), tuple(r[0] for r in rows))}
    con.close()
    return [(e, names.get(e, "?"), n_) for e, n_ in rows]


def sample(base, host_path, n):
    out = []
    for i in range(n):
        s, ms, _ = get(base, host_path, xff=f"9.9.9.{i % 250}")
        if s == 200:
            out.append(ms)
    return out


def stats(v):
    if not v:
        return {"n": 0}
    sv = sorted(v)
    return {"n": len(v), "median": round(statistics.median(v), 1),
            "p95": round(sv[max(0, int(0.95 * len(sv)) - 1)], 1),
            "max": round(max(v), 1)}


def load_window(base, pool, workers, seconds, host_path, host_samples, spoof=True):
    """Run `workers` hostile clients, sampling the host page in the middle.

    ⚠️ Cold every time, for the same reason the curve is — a hostile caller who
    only ever warms one cache entry is not the worst case."""
    stop, res = threading.Event(), []
    lock, seq, seq_lock = threading.Lock(), [10_000], threading.Lock()

    def nxt():
        with seq_lock:
            i = seq[0]
            seq[0] += 1
        return pool[i % len(pool)][0], 1 + (i // len(pool)) % 40

    def hostile(w):
        i = 0
        while not stop.is_set():
            e, k = nxt()
            r = get(base, f"/osint-map/api/graph/{e}?k={k}",
                    xff=(f"10.{w}.{i % 250}.{(i * 7) % 250}" if spoof else "10.0.0.7"))
            with lock:
                res.append(r)
            i += 1

    ts = [threading.Thread(target=hostile, args=(w,), daemon=True) for w in range(workers)]
    for t in ts:
        t.start()
    time.sleep(1.0)                       # let the load establish
    host = sample(base, host_path, host_samples)
    time.sleep(max(0.0, seconds - 1.0))
    stop.set()
    time.sleep(1.0)
    with lock:
        got = list(res)
    ok = [ms for s, ms, _ in got if s == 200]
    codes = Counter(s for s, _, _ in got)
    return host, ok, codes, got


def throughput_curve(base, pool, levels, per_level, serial_control=True):
    """Throughput against concurrency, on an IDENTICAL workload at every level.

    🔴 FLAT OR FALLING THROUGHPUT IS THE SIGNATURE OF SERIALISED WORK.  Latency
    alone can look acceptable while the process is fully queued behind one lock or
    one GIL.

    🔴 AND THE FIRST TWO VERSIONS OF THIS FUNCTION BOTH MEASURED SOMETHING ELSE.
    Version 1 hammered one entity with a repeating `k`, so every request after the
    first was a CACHE HIT and it read 1,401 req/s at concurrency 8.  Version 2
    fixed that with a global counter over a pool sorted by DESCENDING degree — and
    thereby handed concurrency 1 the three heaviest entities in the graph (mean
    degree 15,902) and concurrency 8 much lighter ones (2,931).  It reported
    "scales 3.81x" when most of that was the workload getting 5.4x cheaper.

    ⭐ THE NULL CONTROL IS PART OF THE MEASUREMENT, NOT AN EXTRA.  The same
    workload is also run with NO concurrency at all.  A metric that is sound scores
    that at ~1.0x.  Version 2 scored its own null control at **4.81x** — higher
    than the real number it was reporting — which is how the defect was found and
    is why the control now ships with the tool.  Report neither number alone.
    """
    curve = []
    # ⚠️ WARM THE PAGE CACHE FIRST, OR THE LEVELS MEASURE THE DISK.  Without this
    # the null control drifted 60 -> 110 req/s purely because later levels ran
    # against a warmer OS cache — the control caught it, which is what a control is
    # for, but a metric that needs a control to explain its own noise is not
    # finished. One untimed pass over the same entities removes it.
    for i in range(per_level):
        get(base, f"/osint-map/api/graph/{pool[i % len(pool)][0]}?k=39")
    # the same entities, in the same order, at every level — only the thread count
    # changes.  `k` varies per level purely to keep every request cache-cold; it
    # barely moves the cost, which is dominated by the k-independent census.
    for li, c in enumerate(levels):
        work = [(pool[i % len(pool)][0], 1 + li) for i in range(per_level)]
        curve.append(_run_level(base, work, c, li))
    if serial_control:
        # 🔴 the control runs the UNION of every level's workload one request at a
        # time.  If the "scaling" survives here, it is not scaling.
        for li, c in enumerate(levels):
            work = [(pool[i % len(pool)][0], 40 - li) for i in range(per_level)]
            curve.append(_run_level(base, work, 1, li, control=True))
    return curve


def _run_level(base, work, concurrency, li, control=False):
    res, lock = [], threading.Lock()
    chunks = [work[i::concurrency] for i in range(concurrency)]

    def w(part, wi):
        for j, (e, k) in enumerate(part):
            r = get(base, f"/osint-map/api/graph/{e}?k={k}", xff=f"11.{li}.{wi}.{j}")
            with lock:
                res.append(r)

    ts = [threading.Thread(target=w, args=(chunks[i], i)) for i in range(concurrency)]
    t0 = time.time()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    wall = time.time() - t0
    ok = [ms for s, ms, _ in res if s == 200]
    return {"concurrency": concurrency, "control": control, "ok": len(ok),
            "requests": len(res),
            "median_ms": round(statistics.median(ok), 1) if ok else None,
            "wall_s": round(wall, 2),
            "throughput_rps": round(len(ok) / wall, 2) if wall else None,
            "codes": dict(Counter(s for s, _, _ in res))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8123")
    ap.add_argument("--port", type=int)
    ap.add_argument("--db", required=True, help="the serving snapshot, to pick the worst case")
    ap.add_argument("--host-path", default="/", help="a Scope page to protect")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--seconds", type=float, default=3.0)
    ap.add_argument("--samples", type=int, default=30)
    ap.add_argument("--levels", default="1,2,4,8")
    ap.add_argument("--per-level", type=int, default=24,
                    help="requests per concurrency level — the SAME entities at "
                         "every level, so only the thread count varies")
    ap.add_argument("--no-spoof", action="store_true",
                    help="all clients share one apparent IP, so api/rate_limit.py "
                         "actually bites. The DEFAULT spoofs, because the question "
                         "the gate asks is 'what can a caller do', not 'what does a "
                         "polite caller do' — but both numbers are worth having.")
    ap.add_argument("--pool", type=int, default=40,
                    help="how many top-degree entities to rotate over, so no "
                         "request is served from the neighbourhood cache")
    ap.add_argument("--json")
    a = ap.parse_args()
    base = f"http://127.0.0.1:{a.port}" if a.port else a.base

    pool = worst_case_entities(a.db, a.pool)
    eid, name, deg = pool[0]
    print(f"host page   {a.host_path}")
    print(f"worst case  {name} ({deg:,} raw edge endpoints)  {eid}")
    print(f"pool        top {len(pool)} by degree, rotated so every request is COLD "
          f"(lowest in pool: {pool[-1][1][:28]}, {pool[-1][2]:,})\n")

    for _ in range(240):
        if get(base, "/osint-map/")[0] == 200:
            break
        time.sleep(0.5)
    else:
        raise SystemExit(f"no server answering at {base}")

    baseline = stats(sample(base, a.host_path, a.samples))
    print(f"1. BASELINE   scope {a.host_path}   {baseline}")

    host, ok, codes, raw = load_window(base, pool, a.workers, a.seconds,
                                       a.host_path, a.samples, spoof=not a.no_spoof)
    under = stats(host)
    maplat = stats(ok)
    print(f"2. UNDER LOAD ({a.workers} concurrent, "
          f"{'ONE shared IP — the rate limiter applies' if a.no_spoof else 'IP-spoofed past the limiter'})")
    print(f"   map        {len(raw)} requests  codes={dict(codes)}  {maplat}")
    print(f"   scope      {under}")
    delta = (under.get("median") or 0) - (baseline.get("median") or 0)
    ratio = ((under.get("median") or 0) / baseline["median"]) if baseline.get("median") else None
    print(f"   ⟹ host median {baseline.get('median')} -> {under.get('median')} ms "
          f"({delta:+.1f}, {ratio:.1f}x)" if ratio else "")

    print("\n3. THROUGHPUT vs CONCURRENCY  (identical workload at every level)")
    levels = [int(x) for x in a.levels.split(",")]
    curve = throughput_curve(base, pool, levels, a.per_level)
    real = [c for c in curve if not c["control"]]
    ctrl = [c for c in curve if c["control"]]
    for c in real:
        print(f"   concurrency {c['concurrency']:>2}: {c['ok']}/{c['requests']} ok  "
              f"median {c['median_ms']:>8} ms  wall {c['wall_s']:>5}s  "
              f"{c['throughput_rps']:>7} req/s  codes={c['codes']}")
    scale = ctrl_scale = None
    if real[0]["throughput_rps"] and real[-1]["throughput_rps"]:
        scale = real[-1]["throughput_rps"] / real[0]["throughput_rps"]
        print(f"   ⟹ throughput scales {scale:.2f}x from concurrency "
              f"{real[0]['concurrency']} to {real[-1]['concurrency']}")
    if ctrl and ctrl[0]["throughput_rps"] and ctrl[-1]["throughput_rps"]:
        ctrl_scale = ctrl[-1]["throughput_rps"] / ctrl[0]["throughput_rps"]
        print(f"   NULL CONTROL — the same slices, ZERO concurrency: "
              f"{ctrl[0]['throughput_rps']:.1f} -> {ctrl[-1]['throughput_rps']:.1f} req/s "
              f"= {ctrl_scale:.2f}x")
        print(f"   ⟹ a sound metric scores the control near 1.00x. "
              f"{'🔴 IT DOES NOT — the number above is not measuring concurrency.' if abs(ctrl_scale - 1) > 0.35 else 'OK.'}")

    result = {"host_path": a.host_path, "worst_case": {"entity_id": eid, "name": name,
              "edge_endpoints": deg}, "pool_size": len(pool), "workers": a.workers,
              "baseline": baseline, "under_load_host": under, "map_latency": maplat,
              "map_codes": dict(codes), "throughput_curve": curve,
              "throughput_scale": scale, "null_control_scale": ctrl_scale}
    if a.json:
        json.dump(result, open(a.json, "w"), indent=2)
        print(f"\nwrote {a.json}")


if __name__ == "__main__":
    main()
