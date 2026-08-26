from pprint import pprint

from src.tools import get_default_tools

_, run_sql = get_default_tools()

def main() -> None:
    sql = """
    SELECT
            p.category,
            ROUND(SUM(s.net_revenue), 2) AS total_revenue
        FROM
            `your-project-id.business_insights.fact_sales` AS s
        INNER JOIN
            `your-project-id.business_insights.dim_products` AS p
            ON s.product_id = p.product_id
        GROUP BY
            p.category
        ORDER BY
            total_revenue DESC
        LIMIT 5
    """
    result = run_sql.invoke(
        {
            "sql":sql
        }
    )

    pprint(result)

if __name__ =="__main__":
    main()

    
