#!/bin/bash
# DuckDB E2 and E3 rerun with all eight configurations in one session, so every
# configuration shares the same dashboard-only baseline (PVLDB version).
set -e
cd "$(dirname "$0")"
DB_DIR=${DB_DIR:-/tmp/qg}
TH=5793600
for S in 11 12 13 14 15; do
  [ -f ../results/e2_v2_seed$S.json ] || python3 interfere.py --db "$DB_DIR/analytics.duckdb" --out ../results/e2_v2_seed$S.json --card-threshold $TH --seed $S --duration 120
done
for S in 21 22 23 24 25; do
  [ -f ../results/e3_v2_loop_seed$S.json ] || python3 loop.py --db "$DB_DIR/analytics.duckdb" --out ../results/e3_v2_loop_seed$S.json --card-threshold $TH --seed $S
done
