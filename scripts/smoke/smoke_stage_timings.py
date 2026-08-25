from typing import cast

from src.bigquery_service import BigQueryService
from src.sql_execution_pipeline import (
    SQLExecutionPipeline,
)

def main() -> None:
    service = BigQueryService()

    pipeline = SQLExecutionPipeline(
        bigquery_service=service
    )
    sql = f"""
        SELECT
            p.category,
            ROUND(
                SUM(s.net_revenue),
                2
            ) AS total_revenue
        FROM
            `{service.dataset_path}.fact_sales` AS s
        INNER JOIN
            `{service.dataset_path}.dim_products` AS p
            ON s.product_id = p.product_id
        GROUP BY
            p.category
        ORDER BY
            total_revenue DESC
        LIMIT 5
    """

    result = pipeline.execute(sql)

    print(f"Run ID: {result['run_id']}")
    print(f"Status: {result['status']}")
    print(
        f" Total duration: "
        f"{result['duration_ms']} ms"
    )

    print("\n Stage timings:")

    stage_timings = cast(
        dict[str, float], result['stage_timings_ms']
    )

    for stage_name, duration_ms in stage_timings.items():
        print(
            f"- {stage_name}:"
            f"{duration_ms} ms"
        )

if __name__ =="__main__":
    main()
    
