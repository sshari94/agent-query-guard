"""agent-query-guard: stop AI agents from running runaway SQL on shared databases."""
from .backends import ClickHouseBackend, DuckDBBackend, Estimate, Execution, PostgresBackend
from .guard import Decision, Policy, QueryGuard, Result
from .static import DEFAULT_DENIED_FUNCTIONS, Violation, check

__all__ = [
    "QueryGuard", "Policy", "Decision", "Result", "DuckDBBackend", "PostgresBackend", "ClickHouseBackend",
    "Estimate", "Execution", "Violation", "check", "DEFAULT_DENIED_FUNCTIONS",
]
__version__ = "0.1.0"
