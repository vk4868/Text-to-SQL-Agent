from pprint import pprint
from src.tools import run_sql

def main() -> None:
    test_cases = [
        {
            "name": "Approved table",
            "sql": """
                SELECT
                    category,
                    COUNT(*) AS product_count
                FROM
                    `sql-bigquery-502206.business_insights.dim_products`
                GROUP BY
                    category
                LIMIT 5
            """,
        },
        {
            "name": "Approved CTE",
            "sql": """
                WITH category_totals AS (
                    SELECT
                        product_id,
                        SUM(net_revenue) AS revenue
                    FROM
                        `sql-bigquery-502206.business_insights.fact_sales`
                    GROUP BY
                        product_id
                )
                SELECT *
                FROM category_totals
                LIMIT 5
            """,
        },
        {
            "name": "External project",
            "sql": """
                SELECT *
                FROM
                    `bigquery-public-data.samples.shakespeare`
                LIMIT 5
            """,
        },
        {
            "name": "Unqualified table",
            "sql": """
                SELECT *
                FROM fact_sales
                LIMIT 5
            """,
        },
        {
            "name": "Unknown table",
            "sql": """
                SELECT *
                FROM
                    `sql-bigquery-502206.business_insights.secret_table`
                LIMIT 5
            """,
        },
    ]

    for test_case in test_cases:
        print("=" *70)
        print(f"Test: {test_case['name']}")
        result = run_sql.invoke(
            {
                "sql": test_case["sql"],
            }
        )
        pprint(result)


if __name__ =="__main__":
    main()