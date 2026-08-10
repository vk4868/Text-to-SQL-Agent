"""Stage 3 guardrail: every query returns a bounded number of rows."""

import pytest

from src.sql_validator import enforce_result_limit

TABLE = "`test-project.test_dataset.fact_sales`"


def test_missing_limit_is_injected() -> None:
    result = enforce_result_limit(
        f"SELECT sale_id FROM {TABLE}",
        max_rows=100,
    )

    assert result.is_valid is True
    assert result.was_modified is True
    assert result.effective_limit == 100
    assert result.limited_sql is not None
    assert "LIMIT 100" in result.limited_sql


def test_limit_below_the_maximum_is_left_alone() -> None:
    result = enforce_result_limit(
        f"SELECT sale_id FROM {TABLE} LIMIT 10",
        max_rows=100,
    )

    assert result.is_valid is True
    assert result.was_modified is False
    assert result.effective_limit == 10
    assert result.limited_sql is not None
    assert "LIMIT 10" in result.limited_sql


def test_limit_equal_to_the_maximum_is_left_alone() -> None:
    result = enforce_result_limit(
        f"SELECT sale_id FROM {TABLE} LIMIT 100",
        max_rows=100,
    )

    assert result.is_valid is True
    assert result.was_modified is False
    assert result.effective_limit == 100


def test_limit_above_the_maximum_is_reduced() -> None:
    result = enforce_result_limit(
        f"SELECT sale_id FROM {TABLE} LIMIT 5000",
        max_rows=100,
    )

    assert result.is_valid is True
    assert result.was_modified is True
    assert result.effective_limit == 100
    assert result.limited_sql is not None
    assert "LIMIT 100" in result.limited_sql
    assert "5000" not in result.limited_sql


def test_parameterised_limit_is_rejected() -> None:
    """A parameter means the row bound cannot be reasoned about."""

    result = enforce_result_limit(
        f"SELECT sale_id FROM {TABLE} LIMIT @row_limit",
        max_rows=100,
    )

    assert result.is_valid is False
    assert result.limited_sql is None
    assert "fixed integer" in result.message


def test_calculated_limit_is_rejected() -> None:
    result = enforce_result_limit(
        f"SELECT sale_id FROM {TABLE} LIMIT 10+10",
        max_rows=100,
    )

    assert result.is_valid is False
    assert result.limited_sql is None
    assert "fixed integer" in result.message


def test_limit_only_inside_a_subquery_gets_an_outer_limit() -> None:
    """An inner LIMIT does not bound what the outer query returns."""

    result = enforce_result_limit(
        "SELECT * FROM ("
        f"SELECT sale_id FROM {TABLE} LIMIT 5000"
        ") AS inner_rows",
        max_rows=100,
    )

    assert result.is_valid is True
    assert result.was_modified is True
    assert result.effective_limit == 100
    assert result.limited_sql is not None

    outer_limit = result.limited_sql.rstrip().splitlines()[-1]

    assert outer_limit.strip() == "LIMIT 100"


def test_non_query_statement_is_rejected() -> None:
    result = enforce_result_limit(
        f"DELETE FROM {TABLE} WHERE TRUE",
        max_rows=100,
    )

    assert result.is_valid is False
    assert "only be applied to queries" in result.message


def test_unparseable_sql_is_a_clean_rejection() -> None:
    result = enforce_result_limit("SELEKT *** FROM", max_rows=100)

    assert result.is_valid is False
    assert "Unable to apply result limit" in result.message


@pytest.mark.parametrize("max_rows", [0, -1, -100])
def test_non_positive_maximum_is_a_programming_error(
    max_rows: int,
) -> None:
    with pytest.raises(ValueError):
        enforce_result_limit(
            f"SELECT sale_id FROM {TABLE}",
            max_rows=max_rows,
        )


def test_cte_query_without_a_limit_is_bounded() -> None:
    result = enforce_result_limit(
        "WITH monthly AS ("
        f"SELECT sale_date, net_revenue FROM {TABLE}"
        ") SELECT sale_date FROM monthly",
        max_rows=50,
    )

    assert result.is_valid is True
    assert result.effective_limit == 50
    assert result.limited_sql is not None
    assert "LIMIT 50" in result.limited_sql
