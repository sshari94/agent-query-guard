"""Query workloads: dashboard queries and an AI-agent query corpus.

The agent corpus has benign questions an engineer might ask, plus mutated
versions that reproduce common mistakes in machine-generated SQL (dropped
join predicates, wrong join keys, missing filters, unbounded results,
near-quadratic self-joins). Labels are NOT assigned here: they come from
measuring each query in isolation (see measure.py).
"""
import random

DAY0 = "2026-01-01"


def _day(r, lo=30, hi=175):
    d = r.randint(lo, hi)
    return f"DATE '{DAY0}' + INTERVAL {d} DAY"


def dashboard_queries(seed=1):
    """Short, filtered queries a live operations dashboard refreshes constantly."""
    r = random.Random(seed)
    out = []
    for _ in range(40):
        line = r.randint(0, 23)
        d = _day(r, 150, 178)
        out += [
            f"SELECT s.station_id, count(*) AS n, avg(e.duration_s) AS avg_dur FROM unit_events e JOIN stations s ON e.station_id = s.station_id WHERE s.line_id = {line} AND e.ts >= {d} AND e.ts < {d} + INTERVAL 8 HOUR GROUP BY 1",
            f"SELECT date_trunc('hour', ts) AS h, count(*) FILTER (WHERE result = 'NOK') AS nok, count(*) AS n FROM unit_events WHERE station_id BETWEEN {line * 20} AND {line * 20 + 19} AND ts >= {d} AND ts < {d} + INTERVAL 1 DAY GROUP BY 1 ORDER BY 1",
            f"SELECT reason_code, count(*) FROM scrap WHERE ts >= {d} AND ts < {d} + INTERVAL 1 DAY GROUP BY 1 ORDER BY 2 DESC LIMIT 10",
            f"SELECT avg(CASE WHEN pass THEN 1 ELSE 0 END) AS fpy FROM test_results WHERE attempt = 1 AND ts >= {d} AND ts < {d} + INTERVAL 8 HOUR",
        ]
    return out


BENIGN = [
    # (template id, sql template) ; {line} {site} {lot} {unit} {d} are filled in
    ("B01", "SELECT e.station_id, avg(CASE WHEN e.result = 'OK' THEN 1 ELSE 0 END) AS yield FROM unit_events e JOIN stations s ON e.station_id = s.station_id WHERE s.line_id = {line} AND e.ts >= {d} AND e.ts < {d} + INTERVAL 1 DAY GROUP BY 1 ORDER BY 2"),
    ("B02", "SELECT reason_code, count(*) AS n FROM scrap s JOIN stations st ON s.station_id = st.station_id WHERE st.site_id = {site} AND s.ts >= {d} AND s.ts < {d} + INTERVAL 7 DAY GROUP BY 1 ORDER BY 2 DESC LIMIT 10"),
    ("B03", "SELECT p.family, u.site_id, count(*) FROM units u JOIN products p ON u.product_id = p.product_id WHERE u.created_ts >= {d} AND u.created_ts < {d} + INTERVAL 7 DAY GROUP BY 1, 2"),
    ("B04", "SELECT station_id, avg(CASE WHEN attempt > 1 THEN 1 ELSE 0 END) AS retest_rate FROM test_results WHERE ts >= {d} AND ts < {d} + INTERVAL 30 DAY GROUP BY 1 ORDER BY 2 DESC LIMIT 20"),
    ("B05", "SELECT s.station_type, avg(e.duration_s) FROM unit_events e JOIN stations s ON e.station_id = s.station_id WHERE s.line_id = {line} AND e.ts >= {d} AND e.ts < {d} + INTERVAL 7 DAY GROUP BY 1"),
    ("B06", "SELECT u.unit_id, t.attempt, t.measure_a, t.pass FROM units u JOIN test_results t ON u.unit_id = t.unit_id WHERE u.lot_id = {lot}"),
    ("B07", "SELECT date_trunc('hour', ts) AS h, count(*) FROM unit_events WHERE station_id = {station} AND result = 'NOK' AND ts >= {d} AND ts < {d} + INTERVAL 1 DAY GROUP BY 1 ORDER BY 1"),
    ("B08", "SELECT l.supplier_id, count(s.unit_id) * 1.0 / count(*) AS scrap_rate FROM units u JOIN lots l ON u.lot_id = l.lot_id LEFT JOIN scrap s ON s.unit_id = u.unit_id WHERE u.created_ts >= {d} AND u.created_ts < {d} + INTERVAL 30 DAY GROUP BY 1 ORDER BY 2 DESC LIMIT 10"),
    ("B09", "SELECT p.family, date_trunc('week', t.ts) AS wk, avg(CASE WHEN t.pass THEN 1 ELSE 0 END) AS fpy FROM test_results t JOIN units u ON t.unit_id = u.unit_id JOIN products p ON u.product_id = p.product_id WHERE t.attempt = 1 GROUP BY 1, 2 ORDER BY 1, 2"),
    ("B10", "SELECT e.* FROM unit_events e WHERE e.unit_id = {unit} ORDER BY e.ts"),
    ("B11", "SELECT u.line_id, quantile_cont(t.measure_a, [0.05, 0.5, 0.95]) FROM test_results t JOIN units u ON t.unit_id = u.unit_id WHERE t.ts >= {d} AND t.ts < {d} + INTERVAL 7 DAY GROUP BY 1"),
    ("B12", "SELECT site_id, date_trunc('day', created_ts) AS day, count(*) FROM units GROUP BY 1, 2 ORDER BY 1, 2"),
]

# Mutations reproduce common faults in generated SQL.
MUTANTS = [
    ("X1_cartesian", "SELECT u.unit_id, t.measure_a FROM units u, test_results t WHERE u.lot_id = {lot}"),
    ("X1_cartesian", "SELECT l.supplier_id, count(*) FROM units u, lots l, scrap s WHERE u.created_ts >= {d} AND u.created_ts < {d} + INTERVAL 1 DAY GROUP BY 1"),
    ("X1_cartesian", "SELECT st.site_id, count(*) FROM scrap s CROSS JOIN stations st WHERE s.ts >= {d} GROUP BY 1"),
    ("X2_wrong_key", "SELECT e.unit_id, t.measure_a FROM unit_events e JOIN test_results t ON e.station_id = t.station_id WHERE e.ts >= {d} AND e.ts < {d} + INTERVAL 1 DAY"),
    ("X2_wrong_key", "SELECT u.unit_id, l.material FROM units u JOIN lots l ON u.site_id = l.supplier_id WHERE u.created_ts >= {d} AND u.created_ts < {d} + INTERVAL 7 DAY"),
    ("X2_wrong_key", "SELECT s.reason_code, count(*) FROM scrap s JOIN unit_events e ON s.station_id = e.station_id GROUP BY 1"),
    ("X3_no_filter_highcard", "SELECT unit_id, count(*), avg(duration_s) FROM unit_events GROUP BY 1 ORDER BY 3 DESC"),
    ("X3_no_filter_highcard", "SELECT e.unit_id, max(t.measure_a) FROM unit_events e JOIN test_results t ON e.unit_id = t.unit_id GROUP BY 1"),
    ("X4_unbounded", "SELECT * FROM unit_events"),
    ("X4_unbounded", "SELECT e.*, u.lot_id FROM unit_events e JOIN units u ON e.unit_id = u.unit_id WHERE e.ts >= {d}"),
    ("X5_self_join", "SELECT a.station_id, count(*) FROM unit_events a JOIN unit_events b ON a.station_id = b.station_id AND b.ts BETWEEN a.ts AND a.ts + INTERVAL 1 HOUR WHERE a.ts >= {d} AND a.ts < {d} + INTERVAL 1 DAY GROUP BY 1"),
    ("X5_self_join", "SELECT a.unit_id, b.unit_id FROM units a JOIN units b ON a.lot_id = b.lot_id AND a.unit_id <> b.unit_id WHERE a.site_id = {site}"),
    ("X6_full_sort", "SELECT e.unit_id, e.ts, t.measure_a FROM unit_events e JOIN test_results t ON e.unit_id = t.unit_id ORDER BY t.measure_a DESC"),
]


def _fill(tmpl, r):
    return tmpl.format(
        line=r.randint(0, 23), site=r.randint(0, 2), lot=r.randint(0, 19_999),
        unit=r.randint(0, 1_999_999), station=r.randint(0, 479), d=_day(r))


def agent_corpus(seed=2, benign_per_template=12, mutant_per_template=6):
    r = random.Random(seed)
    corpus = []
    for tid, t in BENIGN:
        for i in range(benign_per_template):
            corpus.append({"qid": f"{tid}_{i}", "family": "benign", "template": tid, "sql": _fill(t, r)})
    for j, (fam, t) in enumerate(MUTANTS):
        for i in range(mutant_per_template):
            corpus.append({"qid": f"M{j:02d}_{i}", "family": fam, "template": f"M{j:02d}", "sql": _fill(t, r)})
    return corpus
