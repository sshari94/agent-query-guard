#!/bin/bash
# Reproduces every experiment in the paper. Takes about 1.5 hours on 4 cores.
set -e
DB_DIR=${DB_DIR:-/tmp/qg}
mkdir -p "$DB_DIR" ../results
[ -f "$DB_DIR/analytics.duckdb" ] || python3 gen_db.py --db "$DB_DIR/analytics.duckdb" --units 2000000 --seed 7
[ -f ../results/e1_ground_truth.json ] || python3 measure.py --db "$DB_DIR/analytics.duckdb" --out ../results/e1_ground_truth.json
TH=5793600  # calibrated on the even half of E1 (see analyze.py)
for S in 11 12 13 14 15; do
  [ -f ../results/e2_seed$S.json ] || python3 interfere.py --db "$DB_DIR/analytics.duckdb" --out ../results/e2_seed$S.json --card-threshold $TH --seed $S --duration 120
done
# Scale check: a database one quarter the size, measured the same way.
[ -f "$DB_DIR/analytics_small.duckdb" ] || python3 gen_db.py --db "$DB_DIR/analytics_small.duckdb" --units 500000 --seed 8
[ -f ../results/e1_ground_truth_small.json ] || python3 measure.py --db "$DB_DIR/analytics_small.duckdb" --out ../results/e1_ground_truth_small.json
# E3: repair-loop sessions with and without the session budget.
for S in 21 22 23 24 25; do
  [ -f ../results/e3_loop_seed$S.json ] || python3 loop.py --db "$DB_DIR/analytics.duckdb" --out ../results/e3_loop_seed$S.json --card-threshold $TH --seed $S
done
