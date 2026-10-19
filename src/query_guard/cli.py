"""Command line: check a query, run one, or serve the guard to agents over MCP."""
from __future__ import annotations

import argparse
import json
import sys

from . import static
from .guard import Policy, QueryGuard


def _policy(args) -> Policy:
    return Policy(
        max_estimated_rows=args.max_estimated_rows,
        timeout_s=args.timeout,
        max_result_rows=args.max_rows,
        session_budget_s=args.session_budget,
        large_tables=frozenset(args.large_table or []),
        denied_tables=frozenset(args.deny_table or []),
        allowed_tables=frozenset(args.allow_table) if args.allow_table else None,
    )


def _guard(args) -> QueryGuard:
    if args.duckdb:
        import duckdb
        con = duckdb.connect(args.duckdb, read_only=True)
        return QueryGuard.for_duckdb(con, _policy(args), audit=args.audit)
    if args.postgres:
        try:
            import psycopg
        except ImportError:
            sys.exit("Postgres support needs: pip install 'agent-query-guard[postgres]'")
        return QueryGuard.for_postgres(psycopg.connect(args.postgres), _policy(args), audit=args.audit)
    sys.exit("Pass --duckdb PATH or --postgres DSN")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="agent-query-guard", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "run", "mcp"):
        p = sub.add_parser(name)
        if name != "mcp":
            p.add_argument("sql")
        p.add_argument("--duckdb", metavar="PATH")
        p.add_argument("--postgres", metavar="DSN")
        p.add_argument("--dialect", help="SQL dialect for check without a database")
        p.add_argument("--max-estimated-rows", type=int, default=50_000_000)
        p.add_argument("--timeout", type=float, default=10.0)
        p.add_argument("--max-rows", type=int, default=10_000)
        p.add_argument("--session-budget", type=float, default=60.0)
        p.add_argument("--large-table", action="append")
        p.add_argument("--deny-table", action="append")
        p.add_argument("--allow-table", action="append")
        p.add_argument("--audit", metavar="FILE", help="append a JSON line per query")
    args = ap.parse_args(argv)

    if args.cmd == "check" and not (args.duckdb or args.postgres):
        p = _policy(args)
        violations = static.check(args.sql, dialect=args.dialect, large_tables=p.large_tables,
                                  denied_tables=p.denied_tables, allowed_tables=p.allowed_tables)
        print(json.dumps({"allowed": not violations, "violations": [str(v) for v in violations]}, indent=2))
        sys.exit(1 if violations else 0)

    guard = _guard(args)
    if args.cmd == "check":
        d = guard.check(args.sql)
        print(json.dumps(d.__dict__, indent=2, default=str))
        sys.exit(0 if d.allowed else 1)
    if args.cmd == "run":
        r = guard.run(args.sql)
        print(json.dumps(r.to_dict(), indent=2, default=str))
        sys.exit(0 if r.status == "ok" else 1)
    from .mcp_server import serve
    serve(guard)


if __name__ == "__main__":
    main()
