from src.bigquery_service import BigQueryService
from src.llm.ollama_client import (
    OllamaGemmaClient,
)
from src.question_to_sql_pipeline import (
    QuestionToSQLPipeline,
)
from src.schema_config import RELATIONSHIPS
from src.schema_provider import SchemaProvider
from src.sql_execution_pipeline import (
    SQLExecutionPipeline,
)
from src.sql_generator import SQLGenerator
from src.sql_repairer import SQLRepairer


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

    sql_repairer = SQLRepairer(
        llm=llm,
    )

    pipeline = QuestionToSQLPipeline(
        schema_provider=schema_provider,
        sql_generator=sql_generator,
        sql_execution_pipeline=(
            sql_execution_pipeline
        ),
        sql_repairer=sql_repairer,
    )

    question = (
        "What were total net sales by month in 2025?"
    )

    result = pipeline.run(question)

    print("Question pipeline result")
    print("-" * 70)
    print(
        f"Question run ID: "
        f"{result['question_run_id']}"
    )
    print(f"Status: {result['status']}")
    print(f"Stage: {result['stage']}")
    print(
        "Pipeline duration: "
        f"{result['question_pipeline_duration_ms']} ms"
    )

    generated_sql = result.get("generated_sql")

    if generated_sql:
        print("\nGenerated SQL:")
        print(generated_sql)

    llm_metadata = result.get("llm_metadata")

    if isinstance(llm_metadata, dict):
        print("\nLLM metadata:")
        print(
            f"Model: "
            f"{llm_metadata.get('model_name')}"
        )
        print(
            f"Total tokens: "
            f"{llm_metadata.get('total_tokens')}"
        )
        print(
            f"Response time: "
            f"{llm_metadata.get('response_time_ms')} ms"
        )

    sql_execution = result.get("sql_execution")

    if isinstance(sql_execution, dict):
        print("\nSQL execution:")
        print(
            f"Status: "
            f"{sql_execution.get('status')}"
        )
        print(
            f"Stage: "
            f"{sql_execution.get('stage')}"
        )
        print(
            f"Rows returned: "
            f"{sql_execution.get('row_count')}"
        )

        print("\nRows:")

        for row in sql_execution.get("rows", []):
            print(row)

    if result.get("message"):
        print("\nError:")
        print(result["message"])


if __name__ == "__main__":
    main()