from src.bigquery_service import BigQueryService
from src.llm.ollama_client import (
    OllamaGemmaClient,
)
from src.schema_config import RELATIONSHIPS
from src.schema_provider import SchemaProvider
from src.sql_repairer import SQLRepairer

def main() -> None:
    bigquery_service = BigQueryService()

    schema_provider =SchemaProvider(
        bigquery_service=bigquery_service,
        relationships=RELATIONSHIPS
    )

    llm = OllamaGemmaClient()

    repairer = SQLRepairer(
        llm=llm
    )
    schema_document = (
        schema_provider.get_schema_document()
    )

    question = (
        "What were total net sales by month in 2025?"
    )

    failed_sql = f"""
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

    error_message = (
        "Unrecognized name: revenue. "
        "The fact_sales table contains net_revenue."
    )

    result = repairer.repair(
        question=question,
        schema_document=schema_document,
        failed_sql=failed_sql,
        error_message=error_message,
        failure_stage="dry_run",
    )

    print("Original SQL:")
    print(result.original_sql)

    print("\nRepaired SQL:")
    print(result.repaired_sql)

    print("\nRepair metadata:")
    print(
        f"Model: "
        f"{result.llm_response.model_name}"
    )
    print(
        f"Input tokens: "
        f"{result.llm_response.input_tokens}"
    )
    print(
        f"Output tokens: "
        f"{result.llm_response.output_tokens}"
    )
    print(
        f"Response time: "
        f"{result.llm_response.response_time_ms} ms"
    )


if __name__ == "__main__":
    main()
