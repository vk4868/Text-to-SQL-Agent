from pprint import pprint

from src.tools import get_default_tools

_, run_sql = get_default_tools()

def main() -> None:
    sql = """
        SELECT
        column_that_does_not_exist
    FROM
        `your-project-id.business_insights.dim_products`
    """
    result = run_sql.invoke(
        {
            "sql":sql
        }
    )
    pprint(result)

if __name__ =="__main__":
    main()
