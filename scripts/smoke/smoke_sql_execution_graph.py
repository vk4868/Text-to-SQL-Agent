from src.bigquery_service import BigQueryService
from src.graph.builder import (
    build_sql_execution_graph,
)
from scripts.smoke._wiring import build_live_nodes
from src.graph.state import AgentState
from src.llm.ollama_client import OllamaGemmaClient
from src.schema_config import RELATIONSHIPS
from src.schema_provider import SchemaProvider
from src.sql_execution_pipeline import (
    SQLExecutionPipeline,
)
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

    sql_execution_pipeline = SQLExecutionPipeline(
        bigquery_service=bigquery_service,
    )

    nodes = build_live_nodes()

    graph = build_sql_execution_graph(
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

    print("Question:")
    print(final_state.get("question"))

    print("\nGenerated SQL:")
    print(final_state.get("generated_sql"))

    print("\nCurrent SQL:")
    print(final_state.get("current_sql"))

    print("\nFinal SQL:")
    print(final_state.get("final_sql"))

    execution_result = final_state.get(
        "execution_result"
    )

    if isinstance(execution_result, dict):
        print("\nExecution status:")
        print(
            execution_result.get("status")
        )

        print("\nExecution stage:")
        print(
            execution_result.get("stage")
        )

        print("\nRows returned:")
        print(
            execution_result.get("row_count")
        )

        print("\nRows:")

        for row in execution_result.get(
            "rows",
            [],
        ):
            print(row)

    print("\nError stage:")
    print(final_state.get("error_stage"))

    print("\nError message:")
    print(final_state.get("error_message"))


if __name__ == "__main__":
    main()