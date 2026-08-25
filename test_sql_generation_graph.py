from src.bigquery_service import BigQueryService
from src.graph.builder import (
    build_sql_generation_graph,
)
from src.graph.nodes import InsightsGraphNodes
from src.graph.state import AgentState
from src.llm.ollama_client import (
    OllamaGemmaClient,
)
from src.schema_config import RELATIONSHIPS
from src.schema_provider import SchemaProvider
from src.sql_generator import SQLGenerator


def main() -> None:
    bigquery_service = BigQueryService()

    schema_provider = SchemaProvider(
        bigquery_service=bigquery_service,
        relationships=RELATIONSHIPS,
    )

    llm = OllamaGemmaClient()

    sql_generator = SQLGenerator(
        llm=llm,
    )

    nodes = InsightsGraphNodes(
        schema_provider=schema_provider,
        sql_generator=sql_generator,
    )

    graph = build_sql_generation_graph(
        nodes=nodes,
    )

    initial_state: AgentState = {
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

    print("Final state keys:")
    print(final_state.keys())

    print("\nQuestion:")
    print(final_state.get("question"))

    print("\nGenerated SQL:")
    print(final_state.get("generated_sql"))

    print("\nCurrent SQL:")
    print(final_state.get("current_sql"))

    print("\nError stage:")
    print(final_state.get("error_stage"))

    print("\nError message:")
    print(final_state.get("error_message"))


if __name__ == "__main__":
    main()