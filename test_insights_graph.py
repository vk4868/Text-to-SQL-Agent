from src.bigquery_service import BigQueryService
from src.graph.builder import build_insights_graph
from src.graph.nodes import InsightsGraphNodes
from src.graph.state import AgentState
from src.llm.ollama_client import OllamaGemmaClient
from src.result_analyzer import ResultAnalyzer
from src.schema_config import RELATIONSHIPS
from src.schema_provider import SchemaProvider
from src.sql_execution_pipeline import (
    SQLExecutionPipeline,
)
from src.sql_generator import SQLGenerator
from src.sql_repairer import SQLRepairer


def main() -> None:
    bigquery_service = BigQueryService()

    llm = OllamaGemmaClient()

    schema_provider = SchemaProvider(
        bigquery_service=bigquery_service,
        relationships=RELATIONSHIPS,
    )

    sql_generator = SQLGenerator(
        llm=llm,
    )

    sql_repairer = SQLRepairer(
        llm=llm,
    )

    result_analyzer = ResultAnalyzer(
        llm=llm,
    )

    sql_execution_pipeline = SQLExecutionPipeline(
        bigquery_service=bigquery_service,
    )

    nodes = InsightsGraphNodes(
        schema_provider=schema_provider,
        sql_generator=sql_generator,
        sql_execution_pipeline=sql_execution_pipeline,
        sql_repairer=sql_repairer,
        result_analyzer=result_analyzer,
    )

    graph = build_insights_graph(
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

    print("\nFinal SQL:")
    print(final_state.get("final_sql"))

    print("\nRepair attempts:")
    print(final_state.get("repair_attempts"))

    print("\nBusiness analysis:")
    print(
        final_state.get(
            "business_analysis"
        )
    )

    print("\nAnalysis metadata:")
    print(
        final_state.get(
            "analysis_metadata"
        )
    )

    print("\nError stage:")
    print(final_state.get("error_stage"))

    print("\nError message:")
    print(final_state.get("error_message"))


if __name__ == "__main__":
    main()