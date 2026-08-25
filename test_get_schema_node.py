from src.bigquery_service import BigQueryService
from src.graph.nodes import InsightsGraphNodes
from src.graph.state import AgentState
from src.schema_config import RELATIONSHIPS
from src.schema_provider import SchemaProvider

def main() -> None:
    bigquery_service = BigQueryService()

    schema_provider = SchemaProvider(
        bigquery_service=bigquery_service,
        relationships= RELATIONSHIPS
    )


    nodes = InsightsGraphNodes(
        schema_provider=schema_provider,
    )

    state: AgentState = {
        "question": (
            "What were total net sales "
            "by month in 2025?"
        ),
        "repair_attempts": 0,
        "repair_history": [],
    }

    update = nodes.get_schema_node(state)

    print("Original state keys:")
    print(state.keys())

    print("\nNode update keys:")
    print(update.keys())

    schema_document = update.get(
        "schema_document"
    )

    if schema_document:
        print("\nSchema retrieved successfully.")
        print("\nFirst 500 characters:")
        print(schema_document[:500])

    if update.get("error_message"):
        print("\nSchema retrieval failed:")
        print(update["error_message"])


if __name__ == "__main__":
    main()



