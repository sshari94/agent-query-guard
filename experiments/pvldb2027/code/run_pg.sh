#!/bin/bash
# PostgreSQL replication of E1-E3. Needs a PostgreSQL 16 server loaded with pg_load.py
# (see README). Run with: bash run_pg.sh
set -e
cd "$(dirname "$0")"
[ -f ../results/e1_pg_ground_truth.json ] || python3 measure_pg.py --out ../results/e1_pg_ground_truth.json
python3 analyze_pg.py --pg ../results/e1_pg_ground_truth.json --duck ../results/e1_ground_truth.json --out ../results/summary_pg_e1.json > /dev/null
TH=2000000  # calibrated on the even half of the PostgreSQL E1 run (analyze_pg.py, est_card)
for S in 11 12 13 14 15; do
  [ -f ../results/e2_pg_seed$S.json ] || python3 interfere_pg.py --out ../results/e2_pg_seed$S.json --card-threshold $TH --seed $S --duration 120
done
for S in 21 22 23 24 25; do
  [ -f ../results/e3_pg_loop_seed$S.json ] || python3 loop_pg.py --out ../results/e3_pg_loop_seed$S.json --card-threshold $TH --seed $S
done
