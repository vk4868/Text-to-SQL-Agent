from src.tools import get_default_tools

_, run_sql = get_default_tools()

def main() -> None:
    test_cases = [
        {
            "name": "No existing limit",
            "sql": """
                SELECT
                    sale_id,
                    sale_date,
                    net_revenue
                FROM
                    `sql-bigquery-502206.business_insights.fact_sales`
                ORDER BY
                    sale_id
            """,
        },
        {
            "name": "Smaller existing limit",
            "sql": """
                SELECT
                    sale_id,
                    sale_date,
                    net_revenue
                FROM
                    `sql-bigquery-502206.business_insights.fact_sales`
                ORDER BY
                    sale_id
                LIMIT 5
            """,
        },
        {
            "name": "Excessive existing limit",
            "sql": """
                SELECT
                    sale_id,
                    sale_date,
                    net_revenue
                FROM
                    `sql-bigquery-502206.business_insights.fact_sales`
                ORDER BY
                    sale_id
                LIMIT 500
            """,
        },
    ]

    for test_case in test_cases:
        result = run_sql.invoke(
            {
                "sql": test_case["sql"],
            }
        )

        print("=" * 70)
        print(f"Test: {test_case['name']}")
        print(f"Status: {result['status']}")
        print(
            "Effective limit: "
            f"{result.get('result_row_limit')}"
        )
        print(
            "Limit modified: "
            f"{result.get('limit_was_modified')}"
        )
        print(
            "Rows returned: "
            f"{result.get('row_count')}"
        )
        print("Executed SQL:")
        print(result.get("executed_sql"))


if __name__ == "__main__":
    main()