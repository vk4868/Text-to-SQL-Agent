from google.cloud import bigquery
import os
from dotenv import load_dotenv
load_dotenv()

project_id = os.getenv("GCP_PROJECT_ID")
dataset_id = "business_insights"


def main() -> None:
    """List dataset columns and run a basic Bigquery query"""

    client = bigquery.Client(project=project_id)
    dataset_reference = f"{project_id}.{dataset_id}"
    print(f"tables in {dataset_reference}")

    tables = list(client.list_tables(dataset_reference))

    for table in tables:
        print(f"{table.table_id}")

    query = f"""
    SELECT COUNT(*) AS row_count,
    MIN(sale_date) as earliest_sale_date,
    MAX(sale_date) AS latest_sale_date from
    `{project_id}.{dataset_id}.fact_sales`
    """

    query_job=client.query(query)
    results = query_job.result()
    print("\nQuery result:")

    for row in results:
        print(f"Row count: {row.row_count}")
        print(f"Earliest sale date : {row.earliest_sale_date}")
        print(f"Latest sale date : {row.latest_sale_date}")


if __name__ == "__main__":
    main()
      