#!/bin/bash
# Single-layer baselines on DuckDB (added for the PVLDB version). Run with: bash run_extra.sh
set -e
cd "$(dirname "$0")"
DB_DIR=${DB_DIR:-/tmp/qg}
TH=5793600
for S in 11 12 13 14 15; do
  [ -f ../results/e2_seed${S}_baselines.json ] || python3 interfere.py --db "$DB_DIR/analytics.duckdb" --out ../results/e2_seed${S}_baselines.json --card-threshold $TH --seed $S --duration 120 --configs dashboard_only,G3_runtime_only,G2_cost_only
done
