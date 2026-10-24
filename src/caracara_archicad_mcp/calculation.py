"""A locked-down, in-memory SQLite database for exact calculations over model data.

Language models add up hundreds of quantities unreliably, so archicad_calculate loads
the numbers into SQLite and lets the model ask for SUM, GROUP BY and the like instead.

The database lives in memory only and is thrown away after each query; nothing is
written to disk. After the tables are filled the connection is made read-only and an
authorizer allows nothing but SELECT: no ATTACH (so no files), no PRAGMA, no writes,
no extension loading. A time budget stops runaway queries, and results are capped.
SQLite ships with Python, so this adds no dependency.
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

MAX_RESULT_ROWS = 500
MAX_SQL_LENGTH = 10_000
QUERY_TIME_BUDGET_SECONDS = 5.0

_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
# load_extension is already disabled by Python; listed so the authorizer refuses it regardless.
_DENIED_FUNCTIONS = {"load_extension"}


class CalculationError(ValueError):
    """The query could not run; the message is meant for the model that wrote it."""


class UnknownNameError(CalculationError):
    """The query names a table or column that does not exist."""


@dataclass
class Table:
    name: str
    columns: dict[str, str]  # column name -> SQLite type (TEXT, INTEGER or REAL)
    rows: list[dict[str, Any]] = field(default_factory=list)


def snake_case(name: str) -> str:
    """'grossVolume' -> 'gross_volume', for column names that read well in SQL."""
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).lower()


def sqlite_type(value: Any) -> str | None:
    """The column type for a value, or None when the value is not a plain number, flag or text."""
    if isinstance(value, bool | int):
        return "INTEGER"
    if isinstance(value, float):
        return "REAL"
    if isinstance(value, str):
        return "TEXT"
    return None


def _authorizer(action: int, arg1: str | None, arg2: str | None, db_name: str | None, trigger: str | None) -> int:
    if action == sqlite3.SQLITE_FUNCTION:
        return sqlite3.SQLITE_DENY if (arg2 or "").lower() in _DENIED_FUNCTIONS else sqlite3.SQLITE_OK
    return sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def describe(tables: Iterable[Table]) -> dict[str, Any]:
    """The tables, their columns and row counts, for the model to write queries against."""
    return {table.name: {"rowCount": len(table.rows), "columns": table.columns} for table in tables}


def column_names(tables: Iterable[Table]) -> dict[str, list[str]]:
    """The tables' column names, short enough to repeat when a query names one that does not exist."""
    return {table.name: list(table.columns) for table in tables}


def run_query(tables: Sequence[Table], sql: str) -> dict[str, Any]:
    """Load the tables into a fresh in-memory database, run one read-only SELECT and discard the database."""
    if len(sql) > MAX_SQL_LENGTH:
        raise CalculationError(f"The query is longer than {MAX_SQL_LENGTH} characters.")
    connection = sqlite3.connect(":memory:")
    try:
        for table in tables:
            columns = list(table.columns)
            definition = ", ".join(f"{_quote(name)} {kind}" for name, kind in table.columns.items())
            connection.execute(f"CREATE TABLE {_quote(table.name)} ({definition})")
            placeholders = ", ".join("?" for _ in columns)
            connection.executemany(
                f"INSERT INTO {_quote(table.name)} VALUES ({placeholders})",
                ([row.get(name) for name in columns] for row in table.rows),
            )
        connection.commit()
        connection.execute("PRAGMA query_only = ON")
        connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_SQL_LENGTH)
        connection.set_authorizer(_authorizer)
        deadline = time.monotonic() + QUERY_TIME_BUDGET_SECONDS
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)

        try:
            cursor = connection.execute(sql)
            rows = cursor.fetchmany(MAX_RESULT_ROWS + 1)
        except sqlite3.Error as error:
            message = str(error).rstrip(".")
            if message == "interrupted":
                message = f"the query took longer than {QUERY_TIME_BUDGET_SECONDS:g} seconds and was stopped"
            elif "not authorized" in message or "authorization denied" in message:
                message = "only a single read-only SELECT query is allowed"
            elif message.startswith(("no such table", "no such column")):
                raise UnknownNameError(f"The query failed: {message}.") from None
            raise CalculationError(f"The query failed: {message}.") from None
        if cursor.description is None:
            raise CalculationError("Only a SELECT query that returns rows is allowed.")
        result: dict[str, Any] = {
            "columns": [column[0] for column in cursor.description],
            "rows": [list(row) for row in rows[:MAX_RESULT_ROWS]],
            "rowCount": min(len(rows), MAX_RESULT_ROWS),
        }
        if len(rows) > MAX_RESULT_ROWS:
            result["truncated"] = True
            result["message"] = f"Only the first {MAX_RESULT_ROWS} rows are returned; aggregate with GROUP BY instead."
        return result
    finally:
        connection.close()
