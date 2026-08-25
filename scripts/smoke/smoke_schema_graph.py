from src import bigquery_service
from src.bigquery_service import BigQueryService
from src.graph.builder import build_schema_graph
from scripts.smoke._wiring import build_live_nodes
from src.graph.state import AgentState
from src.schema_config import RELATIONSHIPS
from src.schema_provider import SchemaProvider

def main() -> None:
    bigquery_service = BigQueryService()

    schema_provider = SchemaProvider(
        bigquery_service=bigquery_service,
        relationships=RELATIONSHIPS
    )

    nodes = build_live_nodes()

    graph = build_schema_graph(
        nodes = nodes
    )

    initial_state:AgentState = {
        "question": (
            "What were total net sales "
            "by month in 2025?"
        ),
        "repair_attempts": 0,
        "repair_history": [],
    }

    final_state = graph.invoke(
        initial_state
    )

    print("Initial state keys:")
    print(initial_state.keys())

    print("\nFinal state keys:")
    print(final_state.keys())

    print("\nQuestion:")
    print(final_state["question"])

    print("\nSchema retrieved:")
    print(
        "schema_document"
        in final_state
    )

    schema_document = final_state.get(
        "schema_document"
    )

    if schema_document:
        print("\nFirst 500 characters:")
        print(schema_document[:500])

    if final_state.get("error_message"):
        print("\nGraph error:")
        print(final_state["error_message"])

if __name__ =="__main__":
    main()

    
    