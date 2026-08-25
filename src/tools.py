"""LangChain tool surface for the agent.

Nothing here runs at import time. Building the BigQuery client eagerly at
module scope made importing this module require live credentials, which broke
test collection for anything that transitively imported it.
"""

from functools import lru_cache

from langchain.tools import tool

from src.bigquery_service import BigQueryService
from src.interfaces import BigQueryReader
from src.schema_config import RELATIONSHIPS
from src.schema_provider import SchemaProvider
from src.sql_execution_pipeline import SQLExecutionPipeline


def build_tools(
    *,
    bigquery_service: BigQueryReader | None = None,
):
    """Build the agent-facing tools over a BigQuery reader.

    Pass a fake ``bigquery_service`` to exercise the tools offline.
    """

    reader: BigQueryReader = bigquery_service or BigQueryService()

    schema_provider = SchemaProvider(
        bigquery_service=reader,
        relationships=RELATIONSHIPS,
    )
    sql_execution_pipeline = SQLExecutionPipeline(
        bigquery_service=reader,
    )

    @tool
    def get_schema() -> str:
        """Return the available bigquery tables, columns, data types,
         table metadata, and supported relationships

         Use this tool before generating SQL so that the table names
         and column names used in the query are based on the actual
         bigquery dataset rather than assumption"""

        return schema_provider.get_schema_document()

    @tool
    def run_sql(sql: str) -> dict[str, object]:
        """Validate, cost-check, and execute one read-only BigQuery query.

        Use this tool only after retrieving the database schema.

        The tool restricts access to approved tables, limits returned
        rows, validates queries through a dry run, enforces processing
        limits, and handles execution errors and timeouts.
        """

        return sql_execution_pipeline.execute(sql)

    return get_schema, run_sql


@lru_cache(maxsize=1)
def get_default_tools():
    """Return the tools bound to the configured live BigQuery dataset."""

    return build_tools()
