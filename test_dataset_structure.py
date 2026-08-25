from pprint import pprint

from src.bigquery_service import BigQueryService

def main() -> None:
    service = BigQueryService()

    dataset_structure = service.get_dataset_structure()
    pprint(dataset_structure)


if __name__ == "__main__":
    main() 