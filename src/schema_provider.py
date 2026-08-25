from src.bigquery_service import BigQueryService
from src.schema_formatter import format_dataset_structure

class SchemaProvider:
    """Build an LLM-friendly document from live bigquery metadata"""

    def __init__(
        self,
        *,
        bigquery_service: BigQueryService,
        relationships: list[str]
    ) -> None:
        self.bigquery_service = bigquery_service
        self.relationships = relationships

    def get_schema_document(self) -> str:
        """Return the current dataset schema as formatted text"""

        dataset_structure = self.bigquery_service.get_dataset_structure()

        return format_dataset_structure(
            dataset_path = self.bigquery_service.dataset_path,
            dataset_structure = dataset_structure,
            relationships = self.relationships
        )

    
