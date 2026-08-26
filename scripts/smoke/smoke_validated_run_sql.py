from pprint import pprint

from src.tools import get_default_tools

_, run_sql = get_default_tools()

def main() -> None:
    test_cases = [
        {
            "name": "Safe SELECT",
            "sql": """
                SELECT
                    category,
                    COUNT(*) AS product_count
                FROM
                    `your-project-id.business_insights.dim_products`
                GROUP BY
                    category
                ORDER BY
                    product_count DESC
                LIMIT 5
            """,
        },
        {
            "name": "Unsafe DELETE",
            "sql": """
                DELETE FROM
                    `your-project-id.business_insights.fact_sales`
                WHERE
                    sale_id = 'S000001'
            """,
        },
        {
            "name": "Unknown column",
            "sql": """
                SELECT
                    column_that_does_not_exist
                FROM
                    `your-project-id.business_insights.dim_products`
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

