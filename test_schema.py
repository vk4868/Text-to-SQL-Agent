from src.bigquery_service import BigQueryService

def main() -> None:
    service = BigQueryService()

    table_name = "fact_sales"
    schema = service.get_table_schema(table_name)

    
    print(f"Schema for {service.dataset_path}.{table_name}")
    print("-" * 70)

    for column in schema:
        description = column["description"] or "No description"

        print(
            f"{column['name']:<22}"
            f"{column['type']:<12}"
            f"{column['mode']:<12}"
            f" {description}"
        )

if __name__ =="__main__":
    main()
