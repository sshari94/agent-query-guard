"""Structural checks that run on the SQL text alone, before the database is touched."""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

# Functions that read files, sleep, or reach outside the database. Agents rarely need them
# and they are a common path to data exfiltration or deliberate slowdowns.
DEFAULT_DENIED_FUNCTIONS = frozenset({
    # DuckDB file and network readers
    "read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto",
    "read_ndjson", "read_text", "read_blob", "glob", "parquet_scan", "httpfs",
    # Postgres
    "pg_sleep", "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "lo_import",
    "lo_export", "dblink", "dblink_exec", "pg_terminate_backend", "pg_cancel_backend",
    "set_config",
})

_WRITE_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter,
    exp.Command, exp.Copy, exp.Set, exp.Use, exp.Transaction, exp.Commit, exp.Rollback,
)


@dataclass(frozen=True)
class Violation:
    rule: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.rule}: {self.detail}" if self.detail else self.rule


def parse(sql: str, dialect: str | None = None) -> exp.Expression:
    """Parse exactly one statement, raising ValueError otherwise."""
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except ParseError as e:
        raise ValueError(f"parse_error: {e.errors[0]['description'] if e.errors else e}") from e
    if len(statements) != 1:
        raise ValueError(f"expected one statement, got {len(statements)}")
    return statements[0]


def function_names(tree: exp.Expression) -> set[str]:
    names = set()
    for f in tree.find_all(exp.Func):
        names.add((f.name if isinstance(f, exp.Anonymous) else f.sql_name()).lower())
    return names


def table_names(tree: exp.Expression) -> set[str]:
    """Physical tables referenced by the query, excluding CTE names."""
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    names = set()
    for t in tree.find_all(exp.Table):
        if t.name and t.name.lower() not in ctes:
            names.add(t.name.lower())
    return names


def check(
    sql: str,
    *,
    dialect: str | None = None,
    read_only: bool = True,
    allowed_tables: frozenset[str] | None = None,
    denied_tables: frozenset[str] = frozenset(),
    denied_functions: frozenset[str] = DEFAULT_DENIED_FUNCTIONS,
    large_tables: frozenset[str] = frozenset(),
    block_cartesian: bool = True,
) -> list[Violation]:
    """Return every rule the query breaks. An empty list means it passed."""
    try:
        tree = parse(sql, dialect)
    except ValueError as e:
        return [Violation("unparsable", str(e))]

    out: list[Violation] = []
    if read_only:
        if not isinstance(tree, exp.Query):
            out.append(Violation("not_read_only", type(tree).__name__.lower()))
        else:
            # Writes can hide inside a read, e.g. a data-modifying CTE in Postgres.
            node = next(tree.find_all(*_WRITE_NODES), None)
            if node is not None:
                out.append(Violation("not_read_only", type(node).__name__.lower()))

    bad_funcs = function_names(tree) & {f.lower() for f in denied_functions}
    if bad_funcs:
        out.append(Violation("denied_function", ", ".join(sorted(bad_funcs))))

    tables = table_names(tree)
    if allowed_tables is not None:
        extra = tables - {t.lower() for t in allowed_tables}
        if extra:
            out.append(Violation("table_not_allowed", ", ".join(sorted(extra))))
    denied = tables & {t.lower() for t in denied_tables}
    if denied:
        out.append(Violation("denied_table", ", ".join(sorted(denied))))

    large = {t.lower() for t in large_tables}
    for sel in tree.find_all(exp.Select):
        if block_cartesian:
            for j in sel.args.get("joins") or []:
                if _is_unconstrained_join(sel, j):
                    out.append(Violation("cartesian_join", j.this.alias_or_name))
                    break
        if large and _is_unbounded_star(sel, large):
            out.append(Violation("unbounded_scan", "SELECT * on a large table with no WHERE or LIMIT"))
    return out


def _is_unconstrained_join(sel: exp.Select, join: exp.Join) -> bool:
    if join.args.get("on") is not None or join.args.get("using"):
        return False
    if (join.args.get("kind") or "").upper() not in ("", "CROSS"):
        return False
    if isinstance(join.this, (exp.Unnest, exp.Lateral)):
        return False
    return not _has_equi_link(sel, join)


def _has_equi_link(sel: exp.Select, join: exp.Join) -> bool:
    """True if a comma- or cross-joined table is tied to another table by an equality in WHERE."""
    where = sel.args.get("where")
    if where is None:
        return False
    alias = join.this.alias_or_name
    for eq in where.find_all(exp.EQ):
        cols = [c for c in (eq.left, eq.right) if isinstance(c, exp.Column)]
        if len(cols) == 2 and alias in {c.table for c in cols} and cols[0].table != cols[1].table:
            return True
    return False


def _is_unbounded_star(sel: exp.Select, large: set[str]) -> bool:
    star = any(isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star))
               for e in sel.expressions)
    if not star or sel.args.get("limit") is not None or sel.args.get("where") is not None:
        return False
    return bool({t.name.lower() for t in sel.find_all(exp.Table)} & large)
