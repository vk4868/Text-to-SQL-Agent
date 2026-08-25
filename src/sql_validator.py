from sqlglot import pretty
from dataclasses import dataclass

from sqlglot import exp, parse, parse_one
from sqlglot.errors import ParseError

@dataclass(frozen=True)
class SQLValidationResult:
    """Represent the result of validating one SQL Query"""

    is_valid: bool
    message:str
    normalized_sql:str | None = None
    referenced_tables: tuple[str,...] =()

@dataclass(frozen=True)
class SQLLimitResult:
    """Represent the result of applying a maximum result row limit"""
    is_valid: bool
    message: str
    limited_sql: str | None = None
    effective_limit: int | None = None
    was_modified: bool = False

def validate_read_only_sql(sql:str) -> SQLValidationResult:
    """
    Validate that SQL contains exactly one read-only query."""

    cleaned_sql = sql.strip()
    if not cleaned_sql:
        return SQLValidationResult(
            is_valid=False,
            message="SQL Query cannot be empty",
        )
    try:
        statements = [
            statement
            for statement in parse(
                cleaned_sql, read="bigquery",
            )
            if statement is not None
        ]
    except ParseError as error:
        return SQLValidationResult(
            is_valid = False,
            message = f"SQL syntax is invalid: {error}",
        )
    
    if len(statements) != 1:
        return SQLValidationResult(
            is_valid = False,
            message = f"Must contain exactly one statement, but found {len(statements)}",
        )
    
    statement = statements[0]

    if not isinstance(statement, exp.Query):
        return SQLValidationResult(
            is_valid=False,
            message=(
                "Only read-only query statements are allowed"
                f"Received: {type(statement).__name__}"
            ),
        )
    normalized_sql = statement.sql(
        dialect="bigquery",
        pretty=True
    )
    return SQLValidationResult(
        is_valid=True,
        message="SQL is valid and read-only",
        normalized_sql=normalized_sql,
    )

def validate_table_access(
    sql:str,
    *,
    allowed_project_id: str,
    allowed_dataset_id: str,
    allowed_table_names: set[str],
) -> SQLValidationResult:

    """Allow physical tables only from the configured bigquery dataset"""
    try:
        statement = parse_one(
            sql, read="bigquery"
        )
    except ParseError as error:
        return SQLValidationResult(
            is_valid = False,
            message = f"Unable to inspect table access: {error}"
        )
    cte_names = {
        cte.alias_or_name
        for cte in statement.find_all(exp.CTE)
    }

    referenced_tables: list[str] =[]

    for table in statement.find_all(exp.Table):
        table_name = table.name
        dataset_id = table.db
        project_id = table.catalog

        is_cte_reference = (
            not project_id
            and not dataset_id
            and table_name in cte_names
        )

        if is_cte_reference:
            continue

        if not project_id or not dataset_id:
            return SQLValidationResult(
                is_valid=False,
                message=(
                    "Every physical table must use a fully qualified "
                    "`project.dataset.table` name. "
                    f"Received: {table.sql(dialect='bigquery')}."
                ),
            )

        if project_id != allowed_project_id:
            return SQLValidationResult(
                is_valid=False,
                message=(
                    f"Project '{project_id}' is not allowed. "
                    f"Only project '{allowed_project_id}' may be queried."
                ),
            )

        if dataset_id != allowed_dataset_id:
            return SQLValidationResult(
                is_valid=False,
                message=(
                    f"Dataset '{dataset_id}' is not allowed. "
                    f"Only dataset '{allowed_dataset_id}' may be queried."
                ),
            )

        if table_name not in allowed_table_names:
            return SQLValidationResult(
                is_valid=False,
                message=(
                    f"Table '{table_name}' is not available in "
                    f"dataset '{allowed_project_id}."
                    f"{allowed_dataset_id}'."
                ),
            )

        full_table_path = (
            f"{project_id}.{dataset_id}.{table_name}"
        )

        referenced_tables.append(full_table_path)

    unique_tables = tuple(
        sorted(set(referenced_tables))
    )

    return SQLValidationResult(
        is_valid=True,
        message="All physical table references are approved.",
        normalized_sql=sql,
        referenced_tables=unique_tables,
    )

def enforce_result_limit(
    sql:str,
    *,
    max_rows: int,
) -> SQLLimitResult:
    """Ensure that a query returns no more than max_rows rows"""

    if max_rows <=0:
        raise ValueError("max_rows must be greater than zero")
    try:
        statement = parse_one(
            sql, read="bigquery"

        )
    except ParseError as error:
        return SQLLimitResult(
            is_valid = False,
            message = f"Unable to apply result limit: {error}",

        )
    if not isinstance(statement,exp.Query):
        return SQLLimitResult(
            is_valid = False,
            message = "Result limits can only be applied to queries"
        )
    limit_clause = statement.args.get("limit")

    if limit_clause is None:
        limited_statement = statement.limit(max_rows)

        return SQLLimitResult(
            is_valid=True,
            message=(
                f"Added a maximum result limit of {max_rows} rows"
            ),
            limited_sql=limited_statement.sql(
                dialect="bigquery",
                pretty=True
            ),
            effective_limit=max_rows,
            was_modified=True,
            )
    limit_expression = limit_clause.expression

    if (
        not isinstance(limit_expression, exp.Literal)
        or not limit_expression.is_int
    ):
        return SQLLimitResult(
            is_valid=False,
            message=(
                "LIMIT must be a fixed integer value. "
                "Parameters or calculated LIMIT values are not allowed."
            ),
        )

    requested_limit = int(limit_expression.this)

    if requested_limit <= max_rows:
        return SQLLimitResult(
            is_valid=True,
            message=(
                f"Existing LIMIT {requested_limit} is within "
                f"the allowed maximum of {max_rows}."
            ),
            limited_sql=statement.sql(
                dialect="bigquery",
                pretty=True,
            ),
            effective_limit=requested_limit,
            was_modified=False,
        )

    limited_statement = statement.limit(max_rows)

    return SQLLimitResult(
        is_valid=True,
        message=(
            f"Reduced LIMIT from {requested_limit} "
            f"to {max_rows} rows."
        ),
        limited_sql=limited_statement.sql(
            dialect="bigquery",
            pretty=True,
        ),
        effective_limit=max_rows,
        was_modified=True,
    )



