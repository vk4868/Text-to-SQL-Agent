from pprint import pprint

from src.tools import run_sql

def main() -> None:
    sql = """
        SELECT
        column_that_does_not_exist
    FROM
        `sql-bigquery-502206.business_insights.dim_products`
    """
    result = run_sql.invoke(
        {
            "sql":sql
        }
    )
    pprint(result)

if __name__ =="__main__":
    main()
