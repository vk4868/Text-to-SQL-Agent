"""MANUAL SCRIPT — requires live BigQuery credentials.

Smoke-checks authentication and configuration by listing the tables in
the configured dataset.

    python scripts/manual/check_bigquery_connection.py
"""

from src.bigquery_service import BigQueryService

def main() -> None:
    service = BigQueryService()

    print(f"Project: {service.project_id}")
    print(f"Dataset : {service.dataset_path}")
    print(f" Location: {service.location}")
    print("Tables:")

    for table_name in service.list_table_names():
        print(f"{table_name}")


if __name__ == "__main__":
    main()
    
