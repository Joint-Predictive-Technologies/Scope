#!/usr/bin/env python3
"""
RULE_10 must accept the scheduler's --emit-alerts flag. Without it, argparse
rejects the flag (exit 2) and RULE_10 — the corroboration engine — fails on every
scheduled run. This locks that invariant.

Runs under pytest or standalone:  python3 tests/test_rule10_emit_alerts.py
"""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_spec = importlib.util.spec_from_file_location(
    "rule_10_corroboration",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "rule_10_corroboration.py"),
)
r10 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r10)


def test_parser_accepts_emit_alerts():
    # Would raise SystemExit(2) before the fix.
    args = r10.build_parser().parse_args(["--emit-alerts"])
    assert args.emit_alerts is True


def test_parser_still_accepts_existing_flags():
    args = r10.build_parser().parse_args(["--dry-run", "--window-hours", "48"])
    assert args.dry_run is True and args.window_hours == 48


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
