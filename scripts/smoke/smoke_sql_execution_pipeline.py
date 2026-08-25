from pprint import pprint

from src.bigquery_service import BigQueryService
from src.sql_execution_pipeline import SQLExecutionPipeline


def main() -> None:
    service = BigQueryService()

    pipeline = SQLExecutionPipeline(
        bigquery_service=service,
    )

    test_cases = [
        {
            "name": "Safe query",
            "sql": f"""
                SELECT
                    category,
                    COUNT(*) AS product_count
                FROM
                    `{service.dataset_path}.dim_products`
                GROUP BY
                    category
                ORDER BY
                    product_count DESC
                LIMIT 5
            """,
        },
        {
            "name": "Unsafe statement",
            "sql": f"""
                DELETE FROM
                    `{service.dataset_path}.fact_sales`
                WHERE
                    sale_id = 'S000001'
            """,
        },
        {
            "name": "Unknown column",
            "sql": f"""
                SELECT
                    unknown_column
                FROM
                    `{service.dataset_path}.dim_products`
            """,
        },
    ]

    for test_case in test_cases:
        print("=" * 70)
        print(f"Test: {test_case['name']}")

        result = pipeline.execute(test_case["sql"])

        pprint(result)


if __name__ == "__main__":
    main()