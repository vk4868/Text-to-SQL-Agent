"""MANUAL SCRIPT — requires live BigQuery credentials and a running Ollama.

Demonstrates the automatic SQL repair loop end to end: SQL generation is
patched to return a deliberately broken query (it selects a column that
does not exist), the guardrail pipeline rejects it at the dry-run stage,
and the repairer asks the LLM for a corrected query.

This script runs real BigQuery jobs and is therefore NOT part of the
pytest suite. Run it deliberately:

    python scripts/manual/run_sql_repair_demo.py

The offline equivalent, which asserts the same repair behaviour against a
stub BigQuery service, is `tests/test_question_to_sql_pipeline.py`.
"""

from unittest.mock import patch

from src.bigquery_service import BigQueryService
from src.llm.base import LLMResponse
from src.llm.ollama_client import OllamaGemmaClient
from src.question_to_sql_pipeline import (
    QuestionToSQLPipeline,
)
from src.schema_config import RELATIONSHIPS
from src.schema_provider import SchemaProvider
from src.sql_execution_pipeline import (
    SQLExecutionPipeline,
)
from src.sql_generator import (
    SQLGenerationResult,
    SQLGenerator,
)
from src.sql_repairer import SQLRepairer

QUESTION = "What were total net sales by month in 2025?"


def main() -> None:
    """Force one broken query through the repair loop and report it."""

    bigquery_service = BigQueryService()

    llm = OllamaGemmaClient()

    schema_provider = SchemaProvider(
        bigquery_service=bigquery_service,
        relationships=RELATIONSHIPS,
    )

    sql_generator = SQLGenerator(
        llm=llm,
    )

    sql_repairer = SQLRepairer(
        llm=llm,
    )

    execution_pipeline = SQLExecutionPipeline(
        bigquery_service=bigquery_service,
    )

    pipeline = QuestionToSQLPipeline(
        schema_provider=schema_provider,
        sql_generator=sql_generator,
        sql_execution_pipeline=execution_pipeline,
        sql_repairer=sql_repairer,
    )

    # `revenue` does not exist; the real column is `net_revenue`.
    # BigQuery's dry run is what catches this.
    bad_sql = f"""
    SELECT
        FORMAT_DATE(
            '%Y-%m',
            sale_date
        ) AS sales_month,
        SUM(revenue) AS total_sales
    FROM
        `{bigquery_service.dataset_path}.fact_sales`
    WHERE
        sale_date >= DATE '2025-01-01'
        AND sale_date < DATE '2026-01-01'
    GROUP BY
        sales_month
    ORDER BY
        sales_month
"""

    fake_generation = SQLGenerationResult(
        question=QUESTION,
        sql=bad_sql,
        raw_model_output=bad_sql,
        llm_response=LLMResponse(
            text=bad_sql,
            model_name="forced-test-output",
        ),
    )

    with patch.object(
        sql_generator,
        "generate",
        return_value=fake_generation,
    ):
        result = pipeline.run(QUESTION)

    print("Status:", result["status"])
    print(
        "Repair attempts:",
        result.get("repair_attempts"),
    )

    print("\nOriginal SQL:")
    print(result.get("generated_sql"))

    print("\nFinal SQL:")
    print(result.get("final_sql"))

    print("\nRepair history:")

    repair_history = result.get(
        "repair_history",
        [],
    )

    if isinstance(repair_history, list):
        for repair in repair_history:
            print(repair)


if __name__ == "__main__":
    main()
