from src.bigquery_service import BigQueryService
from src.llm.ollama_client import (
    OllamaGemmaClient,
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
        relationships=RELATIONSHIPS,
    )

    sql_generator = SQLGenerator(
        llm=llm,
    )

    sql_execution_pipeline = SQLExecutionPipeline(
        bigquery_service=bigquery_service,
    )

    sql_repairer = SQLRepairer(
        llm=llm,
    )

    question_pipeline = QuestionToSQLPipeline(
        schema_provider=schema_provider,
        sql_generator=sql_generator,
        sql_execution_pipeline=(
            sql_execution_pipeline
        ),
        sql_repairer=sql_repairer,
    )

    result_analyzer = ResultAnalyzer(
        llm=llm,
    )

    question = (
        "What were total net sales by month in 2025?"
    )

    question_result = question_pipeline.run(
        question
    )

    if question_result["status"] != "success":
        print("Question-to-SQL pipeline failed.")
        print(question_result)
        return

    sql_execution = question_result["sql_execution"]

    if not isinstance(sql_execution, dict):
        raise TypeError(
            "sql_execution must be a dictionary."
        )

    rows = sql_execution.get("rows", [])

    if not isinstance(rows, list):
        raise TypeError(
            "SQL result rows must be a list."
        )

    analysis_result = result_analyzer.analyze(
        question=question,
        sql=str(question_result["generated_sql"]),
        rows=rows,
        total_result_rows=int(
            sql_execution.get(
                "total_result_rows",
                len(rows),
            )
        ),
    )

    print("Generated SQL:")
    print(question_result["generated_sql"])

    print("\nBusiness analysis:")
    print(analysis_result.analysis)

    print("\nAnalysis metadata:")
    print(
        f"Rows analysed: "
        f"{analysis_result.rows_analyzed}"
    )
    print(
        f"Total result rows: "
        f"{analysis_result.total_result_rows}"
    )
    print(
        f"Rows truncated: "
        f"{analysis_result.rows_were_truncated}"
    )
    print(
        f"Model: "
        f"{analysis_result.llm_response.model_name}"
    )
    print(
        f"Total tokens: "
        f"{analysis_result.llm_response.total_tokens}"
    )
    print(
        f"Response time: "
        f"{analysis_result.llm_response.response_time_ms} ms"
    )


if __name__ == "__main__":
    main()