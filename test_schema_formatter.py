from src.bigquery_service import BigQueryService
from src.schema_formatter import format_dataset_structure


def main() -> None:
    service = BigQueryService()

    dataset_structure = service.get_dataset_structure()

    relationships = [
        (
            "fact_sales.customer_id = "
            "dim_customers.customer_id"
        ),
        (
            "fact_sales.product_id = "
            "dim_products.product_id"
        ),
    ]

    schema_document = format_dataset_structure(
        dataset_path=service.dataset_path,
        dataset_structure=dataset_structure,
        relationships=relationships,
    )

    print(schema_document)


if __name__ == "__main__":
    main()