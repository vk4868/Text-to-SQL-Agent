from src.bigquery_service import BigQueryService
from src.sql_execution_pipeline import (
    SQLExecutionPipeline,
)


def main() -> None:
    service = BigQueryService()

    pipeline = SQLExecutionPipeline(
        bigquery_service=service,
    )

    safe_sql = f"""
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
    """

    safe_result = pipeline.execute(safe_sql)

    print("\nSafe-query result:")
    print(f"Run ID: {safe_result['run_id']}")
    print(f"Status: {safe_result['status']}")
    print(f"Stage: {safe_result['stage']}")
    print(
        f"Duration: {safe_result['duration_ms']} ms"
    )
    print(
        f"Rows returned: {safe_result.get('row_count')}"
    )

    unsafe_sql = f"""
        DELETE FROM
            `{service.dataset_path}.fact_sales`
        WHERE
            sale_id = 'S000001'
    """

    unsafe_result = pipeline.execute(unsafe_sql)

    print("\nUnsafe-query result:")
    print(f"Run ID: {unsafe_result['run_id']}")
    print(f"Status: {unsafe_result['status']}")
    print(f"Stage: {unsafe_result['stage']}")
    print(
        f"Duration: {unsafe_result['duration_ms']} ms"
    )


if __name__ == "__main__":
    main()