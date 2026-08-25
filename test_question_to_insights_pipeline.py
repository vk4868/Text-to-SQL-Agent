from src import sql_execution_pipeline
from src.bigquery_service import BigQueryService
from src.llm.ollama_client import (
    OllamaGemmaClient,
)
from src.question_to_insights_pipeline import (
    QuestionToInsightsPipeline,
)
from src.question_to_sql_pipeline import (
    QuestionToSQLPipeline,
)
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
        relationships = RELATIONSHIPS
    )
    sql_generator = SQLGenerator(
        llm = llm
    )

    sql_execution_pipeline = (
        SQLExecutionPipeline(
            bigquery_service=bigquery_service,
        )
    )

    sql_repairer = SQLRepairer(
        llm=llm,
    )

    question_to_sql_pipeline = (
        QuestionToSQLPipeline(
            schema_provider=schema_provider,
            sql_generator=sql_generator,
            sql_execution_pipeline=(
                sql_execution_pipeline
            ),
            sql_repairer=sql_repairer,
        )
    )
    result_analyzer = ResultAnalyzer(
        llm=llm
    )
    insights_pipeline = (
        QuestionToInsightsPipeline(
            question_to_sql_pipeline = (
                question_to_sql_pipeline
            ),
            result_analyzer=result_analyzer
        )
    )

    question = (
        "What were total net sales by month "
        "in 2025?"
    )

    result = insights_pipeline.run(
        question
    )

    print("Question:")
    print(result.get("question"))

    print("\nStatus:")
    print(result["status"])

    print("\nFinal stage:")
    print(result["stage"])

    print("\nGenerated SQL:")
    print(result.get("generated_sql"))

    print("\nBusiness analysis:")
    print(result.get("business_analysis"))

    print("\nPipeline metadata:")
    print(
        "Insights run ID:",
        result["insights_run_id"],
    )

    print(
        "Total duration:",
        result[
            "insights_pipeline_duration_ms"
        ],
        "ms",
    )

    analysis_metadata = result.get(
        "analysis_metadata"
    )

    if isinstance(analysis_metadata, dict):
        print(
            "Analysis tokens:",
            analysis_metadata.get(
                "total_tokens"
            ),
        )


if __name__ == "__main__":
    main()