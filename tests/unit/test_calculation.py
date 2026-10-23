"""The calculation database: exact answers, and nothing but read-only SELECTs."""

from __future__ import annotations

from pathlib import Path

import pytest

from caracara_archicad_mcp import calculation
from caracara_archicad_mcp.calculation import CalculationError, Table, run_query, snake_case, sqlite_type


@pytest.fixture
def tables():
    return [
        Table(
            "elements",
            {"guid": "TEXT", "type": "TEXT", "story": "TEXT", "volume": "REAL"},
            [
                {"guid": "a", "type": "Wall", "story": "Ground", "volume": 1.5},
                {"guid": "b", "type": "Wall", "story": "First", "volume": 2.25},
                {"guid": "c", "type": "Slab", "story": "Ground", "volume": 3.0},
                {"guid": "d", "type": "Door", "story": "Ground"},
            ],
        )
    ]


def test_aggregates_exactly(tables):
    result = run_query(tables, "SELECT story, SUM(volume) AS m3 FROM elements GROUP BY story ORDER BY story")
    assert result == {"columns": ["story", "m3"], "rows": [["First", 2.25], ["Ground", 4.5]], "rowCount": 2}


def test_missing_values_are_null(tables):
    assert run_query(tables, "SELECT COUNT(volume), COUNT(*) FROM elements")["rows"] == [[3, 4]]


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM elements",
        "UPDATE elements SET volume = 0",
        "INSERT INTO elements (guid) VALUES ('x')",
        "CREATE TABLE other (a)",
        "DROP TABLE elements",
        "PRAGMA table_info(elements)",
        "SELECT load_extension('anything')",
        "BEGIN",
        "VACUUM",
        "SELECT 1; SELECT 2",
    ],
)
def test_anything_but_a_single_select_is_refused(tables, sql):
    with pytest.raises(CalculationError):
        run_query(tables, sql)


@pytest.mark.parametrize("sql", ["ATTACH DATABASE '{path}' AS other", "VACUUM INTO '{path}'"])
def test_no_file_is_ever_written(tables, tmp_path, sql):
    target = tmp_path / "leak.db"
    with pytest.raises(CalculationError):
        run_query(tables, sql.format(path=target))
    assert not Path(target).exists()


def test_runaway_queries_are_stopped(tables, monkeypatch):
    monkeypatch.setattr(calculation, "QUERY_TIME_BUDGET_SECONDS", 0.2)
    with pytest.raises(CalculationError, match="took longer"):
        run_query(tables, "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c")


def test_large_results_are_capped(tables, monkeypatch):
    monkeypatch.setattr(calculation, "MAX_RESULT_ROWS", 2)
    result = run_query(tables, "SELECT guid FROM elements")
    assert result["rowCount"] == 2
    assert result["truncated"] is True


def test_overlong_queries_are_refused(tables):
    with pytest.raises(CalculationError, match="longer than"):
        run_query(tables, "SELECT 1" + " " * calculation.MAX_SQL_LENGTH)


def test_syntax_errors_are_explained(tables):
    with pytest.raises(CalculationError, match="no such column: nope"):
        run_query(tables, "SELECT nope FROM elements")


def test_each_query_starts_from_the_original_data(tables):
    run_query(tables, "SELECT 1")
    assert run_query(tables, "SELECT COUNT(*) FROM elements")["rows"] == [[4]]


def test_column_names_and_types():
    assert snake_case("grossVolume") == "gross_volume"
    assert snake_case("volume") == "volume"
    assert [sqlite_type(v) for v in (True, 3, 1.5, "x", None, [])] == ["INTEGER", "INTEGER", "REAL", "TEXT", None, None]
