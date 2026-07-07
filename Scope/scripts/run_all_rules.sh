#!/bin/bash
# Daily rule runner — execute from the Scope/ directory or via cron.
# Cron example (runs at 7am daily):
#   0 7 * * * cd /Users/avgrusaimer/code/Scope/Scope && bash scripts/run_all_rules.sh >> logs/rules.log 2>&1

set -euo pipefail
cd "$(dirname "$0")/.."

LOGDIR="logs"
mkdir -p "$LOGDIR"
TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")

echo "=== Scope rule run: $TIMESTAMP ==="

echo "[1/6] Ingesting House filings..."
python ingest_house_index.py --emit || echo "WARN: ingest_house_index failed"

echo "[2/6] Running RULE_02 (congressional clusters)..."
python rule_02_cluster.py || echo "WARN: rule_02 failed"

echo "[3/6] Running RULE_06 (SEC Form 4 insider trades)..."
python rule_06_form4.py --emit-alerts || echo "WARN: rule_06 failed"

echo "[4/6] Running RULE_07 (Polymarket)..."
python rule_07_polymarket.py --emit-alerts || echo "WARN: rule_07 failed"

echo "[5/6] Running RULE_08 (Federal Register)..."
python rule_08_federal_register.py --emit-alerts || echo "WARN: rule_08 failed"

echo "[6/6] Running RULE_09 (Lobbying spikes)..."
python rule_09_lobbying.py --emit-alerts || echo "WARN: rule_09 failed"

echo "[7/7] Running RULE_10 (Corroboration + LLM narrative)..."
python scripts/rule_10_corroboration.py || echo "WARN: rule_10 failed"

echo "=== Done: $(date +"%Y-%m-%d %H:%M:%S") ==="
