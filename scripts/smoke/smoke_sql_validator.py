from typing import TypedDict

from src.sql_validator import validate_read_only_sql


class SQLTestCase(TypedDict):
    name: str
    sql: str
    expected_valid: bool


def main() -> None:
    test_cases: list[SQLTestCase] = [
        {
            "name": "Valid SELECT",
            "sql": """
                SELECT
                    category,
                    COUNT(*) AS product_count
                FROM
                    `your-project-id.business_insights.dim_products`
                GROUP BY
                    category
            """,
            "expected_valid": True,
        },
        {
            "name": "Valid CTE",
            "sql": """
                WITH category_sales AS (
                    SELECT
                        product_id,
                        SUM(net_revenue) AS revenue
                    FROM
                        `your-project-id.business_insights.fact_sales`
                    GROUP BY
                        product_id
                )
                SELECT *
                FROM category_sales
            """,
            "expected_valid": True,
        },
        {
            "name": "DELETE statement",
            "sql": """
                DELETE FROM
                    `your-project-id.business_insights.fact_sales`
                WHERE sale_id = 'S000001'
            """,
            "expected_valid": False,
        },
        {
            "name": "CREATE statement",
            "sql": """
                CREATE TABLE
                    `your-project-id.business_insights.temporary_table`
                AS
                SELECT 1 AS value
            """,
            "expected_valid": False,
        },
        {
            "name": "Multiple statements",
            "sql": """
                SELECT 1;
                SELECT 2;
            """,
            "expected_valid": False,
        },
        {
            "name": "Invalid syntax",
            "sql": """
                SELECT (
                FROM
                    `your-project-id.business_insights.fact_sales`
            """,
            "expected_valid": False,
        },
        {
            "name": "Empty SQL",
            "sql": "   ",
            "expected_valid": False,
        },
    ]
    for test_case in test_cases:
        result = validate_read_only_sql(test_case["sql"])
        passed = result.is_valid == test_case["expected_valid"]

        print("=" * 70)
        print(f"Test: {test_case['name']}")
        print(f"Expected valid: {test_case['expected_valid']}")
        print(f"Actual valid: {result.is_valid}")
        print(f"Test passed: {passed}")
        print(f"Message: {result.message}")

        if result.normalized_sql:
            print("Normalized SQL:")
            print(result.normalized_sql)


if __name__ == "__main__":
    main()

