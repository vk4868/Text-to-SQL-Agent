"""The full question -> SQL -> execution -> written analysis workflow."""

import logging

from src.exceptions import LLMProviderError
from src.question_to_insights_pipeline import (
    QuestionToInsightsPipeline,
)
from src.question_to_sql_pipeline import QuestionToSQLPipeline
from src.result_analyzer import ResultAnalyzer
from src.sql_execution_pipeline import SQLExecutionPipeline
from src.sql_generator import SQLGenerator
from src.sql_repairer import SQLRepairer
from tests.conftest import (
    ScriptedLLMClient,
    StubBigQueryService,
    StubSchemaProvider,
)

QUESTION = "What were total net sales by month in 2025?"

GOOD_SQL = (
    "SELECT sale_date FROM "
    "`test-project.test_dataset.fact_sales`"
)

ANALYSIS_TEXT = (
    "DIRECT ANSWER:\nNet sales totalled 1,000 across the period."
)


def build_pipeline(
    *,
    service: StubBigQueryService,
    generator_llm: ScriptedLLMClient,
    analysis_llm: ScriptedLLMClient,
    max_analysis_rows: int = 50,
) -> QuestionToInsightsPipeline:
    logger = logging.getLogger("tests.insights")
    logger.handlers = [logging.NullHandler()]
    logger.propagate = False

    question_pipeline = QuestionToSQLPipeline(
        schema_provider=StubSchemaProvider(),
        sql_generator=SQLGenerator(llm=generator_llm),
        sql_execution_pipeline=SQLExecutionPipeline(
            bigquery_service=service,
            logger=logger,
        ),
        sql_repairer=SQLRepairer(llm=ScriptedLLMClient([])),
        max_repair_attempts=0,
        logger=logger,
    )

    return QuestionToInsightsPipeline(
        question_to_sql_pipeline=question_pipeline,
        result_analyzer=ResultAnalyzer(
            llm=analysis_llm,
            max_analysis_rows=max_analysis_rows,
        ),
        logger=logger,
    )


def test_successful_run_returns_sql_rows_and_analysis(
    stub_bigquery_service: StubBigQueryService,
) -> None:
    pipeline = build_pipeline(
        service=stub_bigquery_service,
        generator_llm=ScriptedLLMClient([GOOD_SQL]),
        analysis_llm=ScriptedLLMClient([ANALYSIS_TEXT]),
    )

    result = pipeline.run(QUESTION)

    assert result["status"] == "success"
    assert result["stage"] == "complete"
    assert result["business_analysis"] == ANALYSIS_TEXT
    assert result["rows"] == [{"sale_id": "S1"}]
    assert result["repair_attempts"] == 0
    assert isinstance(result["insights_run_id"], str)


def test_analysis_metadata_is_reported(
    stub_bigquery_service: StubBigQueryService,
) -> None:
    pipeline = build_pipeline(
        service=stub_bigquery_service,
        generator_llm=ScriptedLLMClient([GOOD_SQL]),
        analysis_llm=ScriptedLLMClient([ANALYSIS_TEXT]),
    )

    result = pipeline.run(QUESTION)

    metadata = result["analysis_metadata"]

    assert isinstance(metadata, dict)
    assert metadata["rows_analyzed"] == 1
    assert metadata["total_result_rows"] == 1
    assert metadata["rows_were_truncated"] is False
    assert metadata["model_name"] == "scripted-model"


def test_the_analysis_prompt_contains_the_question_and_the_rows(
    stub_bigquery_service: StubBigQueryService,
) -> None:
    analysis_llm = ScriptedLLMClient([ANALYSIS_TEXT])

    pipeline = build_pipeline(
        service=stub_bigquery_service,
        generator_llm=ScriptedLLMClient([GOOD_SQL]),
        analysis_llm=analysis_llm,
    )

    pipeline.run(QUESTION)

    prompt = analysis_llm.prompts[0]

    assert QUESTION in prompt
    assert "S1" in prompt
    assert "ROWS WERE TRUNCATED" in prompt


def test_analysis_is_skipped_when_the_sql_stage_fails(
    stub_bigquery_service: StubBigQueryService,
) -> None:
    analysis_llm = ScriptedLLMClient([ANALYSIS_TEXT])

    pipeline = build_pipeline(
        service=stub_bigquery_service,
        generator_llm=ScriptedLLMClient(
            ["SELECT * FROM `other-project.other.table`"]
        ),
        analysis_llm=analysis_llm,
    )

    result = pipeline.run(QUESTION)

    assert result["status"] == "rejected"
    assert result["stage"] == "table_access"
    assert analysis_llm.prompts == []


def test_a_failing_analysis_llm_is_a_structured_error(
    stub_bigquery_service: StubBigQueryService,
) -> None:
    pipeline = build_pipeline(
        service=stub_bigquery_service,
        generator_llm=ScriptedLLMClient([GOOD_SQL]),
        analysis_llm=ScriptedLLMClient(
            [],
            error=LLMProviderError("Ollama is not running"),
        ),
    )

    result = pipeline.run(QUESTION)

    assert result["status"] == "error"
    assert result["stage"] == "result_analysis"
    assert result["error_type"] == "LLMProviderError"


def test_row_truncation_is_declared_to_the_model(
    stub_bigquery_service: StubBigQueryService,
) -> None:
    service = StubBigQueryService(
        rows=[{"sale_id": f"S{index}"} for index in range(10)],
    )
    analysis_llm = ScriptedLLMClient([ANALYSIS_TEXT])

    pipeline = build_pipeline(
        service=service,
        generator_llm=ScriptedLLMClient([GOOD_SQL]),
        analysis_llm=analysis_llm,
        max_analysis_rows=3,
    )

    result = pipeline.run(QUESTION)

    metadata = result["analysis_metadata"]

    assert isinstance(metadata, dict)
    assert metadata["rows_analyzed"] == 3
    assert metadata["total_result_rows"] == 10
    assert metadata["rows_were_truncated"] is True
    assert "ROWS WERE TRUNCATED:\n\nTrue" in analysis_llm.prompts[0]


def test_the_insights_pipeline_writes_its_own_run_record(
    stub_bigquery_service: StubBigQueryService,
    run_records: list[dict[str, object]],
) -> None:
    pipeline = build_pipeline(
        service=stub_bigquery_service,
        generator_llm=ScriptedLLMClient([GOOD_SQL]),
        analysis_llm=ScriptedLLMClient([ANALYSIS_TEXT]),
    )

    result = pipeline.run(QUESTION)

    insight_records = [
        record
        for record in run_records
        if record["event"] == "question_to_insights"
    ]

    assert len(insight_records) == 1

    record = insight_records[0]

    assert record["status"] == "success"
    assert record["question"] == QUESTION
    assert record["insights_run_id"] == result["insights_run_id"]

    # All three layers are observable in one run.
    events = {record["event"] for record in run_records}

    assert events == {
        "sql_execution",
        "question_to_sql",
        "question_to_insights",
    }
