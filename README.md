# agent-query-guard

**Admission control for SQL written by AI agents.** It sits between an LLM agent and your database and refuses queries that are unsafe, too expensive, or part of a runaway chain, before they slow down everyone else on a shared database.

Text-to-SQL agents are now common on warehouses and analytics databases that also serve dashboards and people. Research on data agents (for example *Data Agents Under Attack*, 2026) found uncontrolled query cost to be one of their main failure modes: one bad join or a loop of retries can turn a two-second question into minutes of full-table scans. Read-only database users stop writes, but they do not stop that. This library adds the missing layer.

## See it work

```text
$ python examples/quickstart.py
good aggregate               ok       5 rows, truncated=True
forgot the join condition    blocked  cartesian_join: customers
self-join blow-up            blocked  plan estimates 800,640,512 rows, limit 10,000,000
tries to write               blocked  not_read_only: delete
reads a file                 blocked  denied_function: read_csv
```

## What it checks

| Layer | When | What it stops |
|---|---|---|
| Static rules | Before the database is touched | Writes and DDL, multiple statements, file and network functions (`read_csv`, `pg_read_file`, `dblink`…), tables outside an allow list, accidental cartesian joins, `SELECT *` on large tables with no filter |
| Cost estimate | `EXPLAIN` only, nothing runs | Plans whose estimated row count or cost is above your limit |
| Runtime limits | While running | Queries that exceed a time budget (interrupted), results larger than a row cap (`LIMIT` is pushed into the query) |
| Session budget | Across queries | Agents that keep firing queries: a rolling budget of execution seconds and query count per session |

Every refusal comes back with a machine-readable reason and a human-readable detail, so the agent can rewrite the query instead of failing. An optional JSONL audit log records every decision.

Supported today: **DuckDB** and **PostgreSQL** (any DB-API connection: psycopg 3 or psycopg2). On Postgres each query also runs in a `READ ONLY` transaction with `statement_timeout`, so the guard is not the only line of defence.

## Install

```bash
pip install "agent-query-guard[duckdb]"      # DuckDB
pip install "agent-query-guard[postgres]"    # Postgres
pip install "agent-query-guard[mcp]"         # MCP server for Claude Desktop, Cursor and other MCP clients
```

## Use it in Python

```python
import duckdb
from query_guard import Policy, QueryGuard

con = duckdb.connect("factory.duckdb", read_only=True)
guard = QueryGuard.for_duckdb(
    con,
    Policy(
        max_estimated_rows=20_000_000,   # refuse plans bigger than this
        timeout_s=5,                     # interrupt anything slower
        max_result_rows=1_000,           # cap what goes back to the LLM
        session_budget_s=30,             # execution seconds per session per 5 minutes
        large_tables=frozenset({"events"}),
    ),
    audit="audit.jsonl",
)

result = guard.run(agent_sql, session=conversation_id)
if result.status == "blocked":
    # e.g. "estimated_rows: plan estimates 8,000,000,000 rows, limit 20,000,000"
    feedback_to_agent = f"Query refused ({result.decision.detail}). Filter or aggregate and try again."
else:
    rows = result.rows
```

Use `guard.check(sql)` to get the decision without running anything.

## Use it as an MCP server

Give any MCP-capable agent a guarded database instead of a raw connection:

```bash
agent-query-guard mcp --duckdb factory.duckdb --timeout 5 --max-rows 1000 --audit audit.jsonl
```

Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "factory-db": {
      "command": "agent-query-guard",
      "args": ["mcp", "--duckdb", "/path/to/factory.duckdb", "--max-rows", "1000"]
    }
  }
}
```

The server exposes `run_query`, `check_query` and `list_tables`.

## Use it from the command line

```bash
agent-query-guard check "SELECT * FROM a, b"                    # static rules only, no database
agent-query-guard check --duckdb db.duckdb "SELECT ..."         # adds the cost estimate
agent-query-guard run   --postgres "postgresql://..." "SELECT ..."
```

Exit code is 0 when the query is allowed (or ran successfully) and 1 otherwise, so it also works in CI to lint SQL that agents or people generate.

## Tuning

Start with the audit log in place and generous limits, look at what your agents actually send, then tighten `max_estimated_rows` to just above your largest legitimate query. Optimizer estimates are approximate: they are good at catching queries that are orders of magnitude too big, which is the case that hurts.

## Roadmap

- ClickHouse, Snowflake and BigQuery backends (BigQuery dry-run bytes, Snowflake `EXPLAIN`)
- Per-user and per-team budgets shared across processes (Redis)
- Suggested rewrites in refusals (add a filter on the partition column, aggregate first)
- A public benchmark of agent-generated queries labelled harmful or harmless

Contributions are welcome. Please open an issue first for anything large.

## License

Apache-2.0
