from src.bigquery_service import BigQueryService


def main() -> None:
    service = BigQueryService()

    table_name = "fact_sales"
    metadata = service.get_table_metadata(table_name)

    print(f"Metadata for {service.dataset_path}.{table_name}")
    print("-" * 70)

    for key, value in metadata.items():
        print(f"{key:<28}: {value}")


if __name__ == "__main__":
    main()