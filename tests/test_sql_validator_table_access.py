"""Stage 2 guardrail: only allowlisted tables may be referenced."""

import pytest

from src.sql_validator import validate_table_access
from tests.conftest import (
    ALLOWED_DATASET_ID,
    ALLOWED_PROJECT_ID,
    ALLOWED_TABLE_NAMES,
)


def check(sql: str):
    return validate_table_access(
        sql,
        allowed_project_id=ALLOWED_PROJECT_ID,
        allowed_dataset_id=ALLOWED_DATASET_ID,
        allowed_table_names=set(ALLOWED_TABLE_NAMES),
    )


def test_allowed_table_is_accepted_and_reported() -> None:
    result = check(
        "SELECT sale_id FROM "
        "`test-project.test_dataset.fact_sales`"
    )

    assert result.is_valid is True
    assert result.referenced_tables == (
        "test-project.test_dataset.fact_sales",
    )


def test_join_across_two_allowed_tables_is_accepted() -> None:
    result = check(
        "SELECT f.sale_id, p.product_id "
        "FROM `test-project.test_dataset.fact_sales` AS f "
        "JOIN `test-project.test_dataset.dim_products` AS p "
        "ON f.product_id = p.product_id"
    )

    assert result.is_valid is True
    assert result.referenced_tables == (
        "test-project.test_dataset.dim_products",
        "test-project.test_dataset.fact_sales",
    )


def test_repeated_table_is_reported_once() -> None:
    result = check(
        "SELECT sale_id FROM `test-project.test_dataset.fact_sales` "
        "UNION ALL "
        "SELECT sale_id FROM `test-project.test_dataset.fact_sales`"
    )

    assert result.is_valid is True
    assert len(result.referenced_tables) == 1


def test_unqualified_table_name_is_rejected() -> None:
    result = check("SELECT sale_id FROM fact_sales")

    assert result.is_valid is False
    assert "fully qualified" in result.message


def test_table_in_another_project_is_rejected() -> None:
    result = check(
        "SELECT * FROM "
        "`bigquery-public-data.thelook_ecommerce.orders`"
    )

    assert result.is_valid is False
    assert "bigquery-public-data" in result.message


def test_table_in_another_dataset_is_rejected() -> None:
    result = check(
        "SELECT * FROM `test-project.other_dataset.fact_sales`"
    )

    assert result.is_valid is False
    assert "other_dataset" in result.message


def test_unknown_table_in_allowed_dataset_is_rejected() -> None:
    result = check(
        "SELECT * FROM `test-project.test_dataset.salaries`"
    )

    assert result.is_valid is False
    assert "salaries" in result.message


def test_foreign_table_hidden_in_a_scalar_subquery_is_rejected() -> None:
    """The AST walk reaches nested tables, not just the FROM clause."""

    result = check(
        "SELECT sale_id, "
        "(SELECT MAX(id) FROM `other-project.secrets.api_keys`) AS leaked "
        "FROM `test-project.test_dataset.fact_sales`"
    )

    assert result.is_valid is False
    assert "other-project" in result.message


def test_foreign_table_hidden_in_a_cte_is_rejected() -> None:
    result = check(
        "WITH leak AS ("
        "SELECT * FROM `other-project.secrets.api_keys`"
        ") SELECT * FROM leak"
    )

    assert result.is_valid is False
    assert "other-project" in result.message


def test_information_schema_is_rejected() -> None:
    result = check(
        "SELECT * FROM "
        "`test-project.test_dataset.INFORMATION_SCHEMA.TABLES`"
    )

    assert result.is_valid is False
    assert "INFORMATION_SCHEMA" in result.message


def test_wildcard_table_is_rejected() -> None:
    """`fact_sales_*` is not the allowlisted `fact_sales`."""

    result = check(
        "SELECT * FROM `test-project.test_dataset.fact_sales_*`"
    )

    assert result.is_valid is False
    assert "fact_sales_*" in result.message


def test_cte_alias_is_not_mistaken_for_a_physical_table() -> None:
    result = check(
        "WITH monthly_sales AS ("
        "SELECT sale_date, net_revenue "
        "FROM `test-project.test_dataset.fact_sales`"
        ") SELECT sale_date FROM monthly_sales"
    )

    assert result.is_valid is True
    assert result.referenced_tables == (
        "test-project.test_dataset.fact_sales",
    )


def test_chained_cte_aliases_are_skipped() -> None:
    result = check(
        "WITH base AS ("
        "SELECT sale_id FROM `test-project.test_dataset.fact_sales`"
        "), summary AS (SELECT COUNT(*) AS n FROM base) "
        "SELECT n FROM summary"
    )

    assert result.is_valid is True


def test_unnest_is_not_treated_as_a_table() -> None:
    result = check(
        "SELECT value FROM UNNEST([1, 2, 3]) AS value"
    )

    assert result.is_valid is True
    assert result.referenced_tables == ()


def test_unnest_alongside_an_allowed_table_is_accepted() -> None:
    result = check(
        "SELECT f.sale_id, tag "
        "FROM `test-project.test_dataset.fact_sales` AS f, "
        "UNNEST(f.tags) AS tag"
    )

    assert result.is_valid is True
    assert result.referenced_tables == (
        "test-project.test_dataset.fact_sales",
    )


def test_unparseable_sql_is_a_clean_rejection() -> None:
    result = check("SELEKT *** FROM")

    assert result.is_valid is False
    assert "Unable to inspect table access" in result.message


@pytest.mark.parametrize(
    "table_name",
    ALLOWED_TABLE_NAMES,
)
def test_every_allowlisted_table_is_queryable(table_name: str) -> None:
    result = check(
        f"SELECT * FROM `test-project.test_dataset.{table_name}`"
    )

    assert result.is_valid is True
