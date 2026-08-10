"""Stage 1 guardrail: only a single read-only query may pass.

The rejection cases are the adversarial set the project is designed to
stop: every DML/DDL verb, statement stacking, and comment-based tricks.
"""

import pytest
from sqlglot import exp, parse

from src.sql_validator import validate_read_only_sql

TABLE = "`test-project.test_dataset.fact_sales`"

REJECTED_STATEMENTS = {
    "delete": f"DELETE FROM {TABLE} WHERE TRUE",
    "insert": f"INSERT INTO {TABLE} (sale_id) VALUES ('x')",
    "update": f"UPDATE {TABLE} SET sale_id = 'x' WHERE TRUE",
    "drop": f"DROP TABLE {TABLE}",
    "create_table_as_select": (
        "CREATE TABLE `test-project.test_dataset.copy` AS "
        f"SELECT * FROM {TABLE}"
    ),
    "merge": (
        f"MERGE {TABLE} AS target USING "
        "`test-project.test_dataset.dim_products` AS source "
        "ON target.product_id = source.product_id "
        "WHEN MATCHED THEN UPDATE SET target.sale_id = source.product_id"
    ),
    "truncate": f"TRUNCATE TABLE {TABLE}",
    "grant": (
        "GRANT `roles/bigquery.dataViewer` "
        f"ON TABLE {TABLE} TO 'user:attacker@example.com'"
    ),
    "export_data": (
        "EXPORT DATA OPTIONS(uri='gs://bucket/leak/*.csv') AS "
        f"SELECT * FROM {TABLE}"
    ),
    "call_procedure": (
        "CALL `test-project.test_dataset.some_procedure`()"
    ),
    "alter": f"ALTER TABLE {TABLE} ADD COLUMN injected STRING",
}


@pytest.mark.parametrize(
    "sql",
    list(REJECTED_STATEMENTS.values()),
    ids=list(REJECTED_STATEMENTS),
)
def test_non_read_only_statements_are_rejected(sql: str) -> None:
    result = validate_read_only_sql(sql)

    assert result.is_valid is False
    assert result.normalized_sql is None


def test_rejection_message_is_readable_for_the_repair_prompt() -> None:
    """Messages are fed verbatim to the LLM, so they must not be mangled."""

    result = validate_read_only_sql(f"DELETE FROM {TABLE} WHERE TRUE")

    assert "allowedReceived" not in result.message
    assert "Only read-only query statements are allowed." in result.message
    assert "Received: Delete." in result.message


def test_begin_end_block_is_rejected() -> None:
    result = validate_read_only_sql("BEGIN SELECT 1; END")

    assert result.is_valid is False


def test_stacked_statements_are_rejected() -> None:
    result = validate_read_only_sql(f"SELECT 1; DROP TABLE {TABLE}")

    assert result.is_valid is False
    assert "exactly one statement" in result.message


def test_comment_hidden_drop_is_inert_and_never_reaches_bigquery() -> None:
    """A DROP inside a trailing comment is neutralised, not executed.

    The query itself is legitimate, so it is accepted — but the
    normalized SQL that downstream stages use contains no DROP.
    """

    result = validate_read_only_sql(
        f"SELECT sale_id FROM {TABLE} -- ; DROP TABLE {TABLE}"
    )

    assert result.is_valid is True
    assert result.normalized_sql is not None
    assert "DROP" not in result.normalized_sql.upper()


def test_block_comment_hidden_drop_stays_a_comment() -> None:
    """Text inside a block comment never becomes a second statement.

    sqlglot preserves the comment when it re-renders the SQL, so the
    guarantee that matters is structural: what BigQuery receives is still
    exactly one SELECT.
    """

    result = validate_read_only_sql(
        f"SELECT sale_id /* ; DROP TABLE {TABLE} */ FROM {TABLE}"
    )

    assert result.is_valid is True
    assert result.normalized_sql is not None

    statements = parse(result.normalized_sql, read="bigquery")

    assert len(statements) == 1
    assert isinstance(statements[0], exp.Select)


@pytest.mark.parametrize("sql", ["", "   ", "\n\t "])
def test_empty_sql_is_rejected(sql: str) -> None:
    result = validate_read_only_sql(sql)

    assert result.is_valid is False
    assert result.message == "SQL Query cannot be empty"


def test_unparseable_sql_is_a_clean_rejection_not_a_crash() -> None:
    result = validate_read_only_sql("SELEKT *** FROM")

    assert result.is_valid is False
    assert "SQL syntax is invalid" in result.message


def test_plain_select_is_accepted_and_normalized() -> None:
    result = validate_read_only_sql(
        f"select sale_id from {TABLE}"
    )

    assert result.is_valid is True
    assert result.message == "SQL is valid and read-only"
    assert result.normalized_sql is not None
    assert "SELECT" in result.normalized_sql


def test_cte_query_is_accepted() -> None:
    result = validate_read_only_sql(
        "WITH monthly AS ("
        f"SELECT sale_date, net_revenue FROM {TABLE}"
        ") SELECT sale_date FROM monthly"
    )

    assert result.is_valid is True


def test_union_query_is_accepted() -> None:
    result = validate_read_only_sql(
        f"SELECT sale_id FROM {TABLE} "
        f"UNION ALL SELECT sale_id FROM {TABLE}"
    )

    assert result.is_valid is True
