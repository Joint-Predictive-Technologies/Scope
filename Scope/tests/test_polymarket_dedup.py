#!/usr/bin/env python3
"""
Unit tests for Polymarket (RULE_07) alert deduplication.

Runs under pytest or standalone:  python3 tests/test_polymarket_dedup.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "rule_07_polymarket",
    os.path.join(os.path.dirname(__file__), "..", "rule_07_polymarket.py"),
)
r07 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r07)


def _row(direction="up", bucket=0, volume=600000, age_hours=1):
    import datetime
    ts = (datetime.datetime.utcnow() - datetime.timedelta(hours=age_hours)).isoformat()
    return {
        "id": 1,
        "created_at": ts,
        "tags": json.dumps({"market_id": "m1", "direction_sign": direction,
                            "bucket": bucket, "volume": volume}),
    }


def test_market_id_stable():
    assert r07.market_id_of({"conditionId": "0xabc"}) == "0xabc"
    assert r07.market_id_of({"id": 42}) == "42"
    assert r07.market_id_of({"slug": "will-x"}) == "will-x"


def test_threshold_buckets():
    assert r07.threshold_bucket(21) == 0
    assert r07.threshold_bucket(35) == 1
    assert r07.threshold_bucket(60) == 2


def test_first_time_is_new():
    assert r07.dedup_decision(None, "up", 0, 600000) == "new"


def test_repeated_snapshot_updates_not_new():
    # Same direction/bucket/volume within cooldown → update (no duplicate alert)
    ex = _row(direction="up", bucket=0, volume=600000, age_hours=1)
    assert r07.dedup_decision(ex, "up", 0, 600000) == "update"


def test_direction_flip_is_new():
    ex = _row(direction="up", bucket=0, volume=600000, age_hours=1)
    assert r07.dedup_decision(ex, "down", 0, 600000) == "new"


def test_higher_bucket_is_new():
    ex = _row(direction="up", bucket=0, volume=600000, age_hours=1)
    assert r07.dedup_decision(ex, "up", 1, 600000) == "new"


def test_lower_bucket_still_update():
    ex = _row(direction="up", bucket=2, volume=600000, age_hours=1)
    assert r07.dedup_decision(ex, "up", 0, 600000) == "update"


def test_material_volume_change_is_new():
    ex = _row(direction="up", bucket=0, volume=600000, age_hours=1)
    assert r07.dedup_decision(ex, "up", 0, 1000000) == "new"  # +66%


def test_cooldown_elapsed_is_new():
    ex = _row(direction="up", bucket=0, volume=600000, age_hours=30)
    assert r07.dedup_decision(ex, "up", 0, 600000) == "new"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} tests passed")


if __name__ == "__main__":
    _run_all()
