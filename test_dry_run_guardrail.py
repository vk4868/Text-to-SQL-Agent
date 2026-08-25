from pprint import pprint

from src.tools import run_sql

def main() -> None:
    sql = """
    SELECT
            p.category,
            ROUND(SUM(s.net_revenue), 2) AS total_revenue
        FROM
            `sql-bigquery-502206.business_insights.fact_sales` AS s
        INNER JOIN
            `sql-bigquery-502206.business_insights.dim_products` AS p
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

    
