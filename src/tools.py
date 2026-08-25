from typing import cast


from src.schema_provider import SchemaProvider
from langchain.tools import tool

from src.bigquery_service import BigQueryService
from src.schema_config import RELATIONSHIPS

from src.sql_execution_pipeline import SQLExecutionPipeline




bigquery_service = BigQueryService()
schema_provider = SchemaProvider(
    bigquery_service = bigquery_service,
    relationships=RELATIONSHIPS
)
sql_execution_pipeline = SQLExecutionPipeline(
    bigquery_service=bigquery_service,
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
