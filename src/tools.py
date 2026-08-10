"""LangChain tool surface for the agent.

The BigQuery client, schema provider, and execution pipeline are built
lazily on first use. Importing this module therefore never contacts
Google Cloud, which keeps the pure-logic modules testable offline and
means a missing credential is reported when a tool is actually called
rather than at import time.
"""

from functools import lru_cache

from langchain.tools import tool

from src.bigquery_service import BigQueryService
from src.schema_config import RELATIONSHIPS
from src.schema_provider import SchemaProvider
from src.sql_execution_pipeline import SQLExecutionPipeline


@lru_cache(maxsize=1)
def get_bigquery_service() -> BigQueryService:
    """Return the shared BigQueryService, creating it on first use."""

    return BigQueryService()


@lru_cache(maxsize=1)
def get_schema_provider() -> SchemaProvider:
    """Return the shared SchemaProvider, creating it on first use."""

    return SchemaProvider(
        bigquery_service=get_bigquery_service(),
        relationships=RELATIONSHIPS,
    )


@lru_cache(maxsize=1)
def get_sql_execution_pipeline() -> SQLExecutionPipeline:
    """Return the shared SQLExecutionPipeline, created on first use."""

    return SQLExecutionPipeline(
        bigquery_service=get_bigquery_service(),
    )


def reset_tool_dependencies() -> None:
    """Clear the cached singletons.

    Useful in tests and after changing configuration at runtime.
    """

    get_bigquery_service.cache_clear()
    get_schema_provider.cache_clear()
    get_sql_execution_pipeline.cache_clear()


@tool
def get_schema() -> str:
    """Return the available bigquery tables, columns, data types,
     table metadata, and supported relationships

     Use this tool before generating SQL so that the table names
     and column names used in the query are based on the actual
     bigquery dataset rather than assumption"""

    return get_schema_provider().get_schema_document()


@tool
def run_sql(sql: str) -> dict[str, object]:
    """Validate, cost-check, and execute one read-only BigQuery query.

    Use this tool only after retrieving the database schema.

    The tool restricts access to approved tables, limits returned
    rows, validates queries through a dry run, enforces processing
    limits, and handles execution errors and timeouts.
    """

    return get_sql_execution_pipeline().execute(sql)
