"""Run: pip install "agent-query-guard[duckdb]" && python examples/quickstart.py"""
import duckdb

from query_guard import Policy, QueryGuard

con = duckdb.connect()
con.execute("CREATE TABLE orders AS SELECT range AS id, range % 1000 AS customer_id, random() * 100 AS amount "
            "FROM range(1000000)")
con.execute("CREATE TABLE customers AS SELECT range AS id, 'customer-' || range AS name FROM range(1000)")

guard = QueryGuard.for_duckdb(con, Policy(max_estimated_rows=10_000_000, max_result_rows=5, timeout_s=2))

queries = {
    "good aggregate": "SELECT c.name, sum(o.amount) AS total FROM orders o JOIN customers c "
                      "ON o.customer_id = c.id GROUP BY 1 ORDER BY 2 DESC",
    "forgot the join condition": "SELECT * FROM orders, customers",
    "self-join blow-up": "SELECT count(*) FROM orders a JOIN orders b ON a.customer_id = b.customer_id",
    "tries to write": "DELETE FROM orders",
    "reads a file": "SELECT * FROM read_csv('/etc/passwd')",
}
for label, sql in queries.items():
    r = guard.run(sql, session="demo")
    print(f"{label:28} {r.status:8} {r.decision.detail or f'{len(r.rows)} rows, truncated={r.truncated}'}")
