from pprint import pprint

from src.bigquery_service import BigQueryService

def main() -> None:
    service = BigQueryService()

    sql = f"""
        SELECT
            p.category,
            ROUND(SUM(s.net_revenue), 2) AS total_revenue,
            ROUND(SUM(s.profit_amount), 2) AS total_profit
        FROM `{service.dataset_path}.fact_sales` AS s
        INNER JOIN `{service.dataset_path}.dim_products` AS p
            ON s.product_id = p.product_id
        GROUP BY
            p.category
        ORDER BY
            total_revenue DESC
        LIMIT 5
    """
    result = service.run_query(sql)

    print("Query rows:")
    pprint(result["rows"])

    print("\n Query metadata")
    print(f"Rows returned: {result['row_count']}")
    print(f"Job ID: {result['job_id']}")
    print(f"Statement type: {result['statement_type']}")
    print(
        "Bytes processed: "
        f"{result['total_bytes_processed']}"
    )
    print("Bytes billed: "
        f"{result['total_bytes_billed']}"
    )
    print(f"Cache hit: {result['cache_hit']}")

if __name__ == "__main__":
    main()

