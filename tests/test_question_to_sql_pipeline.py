"""Question -> SQL -> execution, including the LLM repair loop."""

import logging

from google.api_core.exceptions import BadRequest

from src.exceptions import LLMProviderError
from src.question_to_sql_pipeline import QuestionToSQLPipeline
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

FOREIGN_TABLE_SQL = (
    "SELECT * FROM `bigquery-public-data.thelook_ecommerce.orders`"
)

UNQUALIFIED_SQL = "SELECT sale_date FROM fact_sales"


def build_pipeline(
    *,
    service: StubBigQueryService,
    generator_llm: ScriptedLLMClient,
    repairer_llm: ScriptedLLMClient | None = None,
    schema_provider: StubSchemaProvider | None = None,
    max_repair_attempts: int = 2,
) -> QuestionToSQLPipeline:
    logger = logging.getLogger("tests.question")
    logger.handlers = [logging.NullHandler()]
    logger.propagate = False

    return QuestionToSQLPipeline(
        schema_provider=schema_provider or StubSchemaProvider(),
        sql_generator=SQLGenerator(llm=generator_llm),
        sql_execution_pipeline=SQLExecutionPipeline(
            bigquery_service=service,
            logger=logger,
        ),
        sql_repairer=SQLRepairer(
            llm=repairer_llm or ScriptedLLMClient([]),
        ),
        max_repair_attempts=max_repair_attempts,
        logger=logger,
    )


def test_empty_question_is_rejected_before_any_work(
    stub_bigquery_service: StubBigQueryService,
) -> None:
    generator_llm = ScriptedLLMClient([GOOD_SQL])

    pipeline = build_pipeline(
        service=stub_bigquery_service,
        generator_llm=generator_llm,
    )

    result = pipeline.run("   ")

    assert result["status"] == "rejected"
    assert result["stage"] == "question_validation"
    assert generator_llm.prompts == []
    assert stub_bigquery_service.run_query_calls == []


def test_good_sql_runs_without_any_repair(
    stub_bigquery_service: StubBigQueryService,
) -> None:
    pipeline = build_pipeline(
        service=stub_bigquery_service,
        generator_llm=ScriptedLLMClient([GOOD_SQL]),
    )

    result = pipeline.run(QUESTION)

    assert result["status"] == "success"
    assert result["repair_attempts"] == 0
    assert result["repair_history"] == []
    assert result["generated_sql"] == result["final_sql"]


def test_markdown_fences_are_stripped_before_validation(
    stub_bigquery_service: StubBigQueryService,
) -> None:
    pipeline = build_pipeline(
        service=stub_bigquery_service,
        generator_llm=ScriptedLLMClient(
            [f"```sql\n{GOOD_SQL}\n```"]
        ),
    )

    result = pipeline.run(QUESTION)

    assert result["status"] == "success"
    assert "```" not in str(result["generated_sql"])


def test_rejected_sql_is_repaired_and_then_succeeds(
    stub_bigquery_service: StubBigQueryService,
) -> None:
    pipeline = build_pipeline(
        service=stub_bigquery_service,
        generator_llm=ScriptedLLMClient([FOREIGN_TABLE_SQL]),
        repairer_llm=ScriptedLLMClient([GOOD_SQL]),
    )

    result = pipeline.run(QUESTION)

    assert result["status"] == "success"
    assert result["repair_attempts"] == 1
    assert result["generated_sql"] != result["final_sql"]

    repair = result["repair_history"][0]  # type: ignore[index]

    assert repair["failure_stage"] == "table_access"
    assert repair["repair_attempt"] == 1
    assert "bigquery-public-data" in repair["error_message"]


def test_the_repair_prompt_receives_the_real_error_message(
    stub_bigquery_service: StubBigQueryService,
) -> None:
    """The rejection text is what tells the model what to fix."""

    repairer_llm = ScriptedLLMClient([GOOD_SQL])

    pipeline = build_pipeline(
        service=stub_bigquery_service,
        generator_llm=ScriptedLLMClient([UNQUALIFIED_SQL]),
        repairer_llm=repairer_llm,
    )

    pipeline.run(QUESTION)

    prompt = repairer_llm.prompts[0]

    assert "fully qualified" in prompt
    assert "FAILURE STAGE" in prompt
    assert "table_access" in prompt


def test_two_repairs_are_attempted_before_success(
    stub_bigquery_service: StubBigQueryService,
) -> None:
    pipeline = build_pipeline(
        service=stub_bigquery_service,
        generator_llm=ScriptedLLMClient([FOREIGN_TABLE_SQL]),
        repairer_llm=ScriptedLLMClient(
            [UNQUALIFIED_SQL, GOOD_SQL]
        ),
        max_repair_attempts=2,
    )

    result = pipeline.run(QUESTION)

    assert result["status"] == "success"
    assert result["repair_attempts"] == 2


def test_repair_budget_is_exhausted_and_the_failure_is_returned(
    stub_bigquery_service: StubBigQueryService,
) -> None:
    pipeline = build_pipeline(
        service=stub_bigquery_service,
        generator_llm=ScriptedLLMClient([FOREIGN_TABLE_SQL]),
        repairer_llm=ScriptedLLMClient(
            [FOREIGN_TABLE_SQL, FOREIGN_TABLE_SQL]
        ),
        max_repair_attempts=2,
    )

    result = pipeline.run(QUESTION)

    assert result["status"] == "rejected"
    assert result["stage"] == "table_access"
    assert result["repair_attempts"] == 2
    assert len(result["repair_history"]) == 2  # type: ignore[arg-type]
    assert stub_bigquery_service.run_query_calls == []


def test_repairs_can_be_disabled(
    stub_bigquery_service: StubBigQueryService,
) -> None:
    repairer_llm = ScriptedLLMClient([GOOD_SQL])

    pipeline = build_pipeline(
        service=stub_bigquery_service,
        generator_llm=ScriptedLLMClient([FOREIGN_TABLE_SQL]),
        repairer_llm=repairer_llm,
        max_repair_attempts=0,
    )

    result = pipeline.run(QUESTION)

    assert result["status"] == "rejected"
    assert result["repair_attempts"] == 0
    assert repairer_llm.prompts == []


def test_dry_run_failures_are_repairable(
    stub_bigquery_service: StubBigQueryService,
) -> None:
    """A hallucinated column is caught by BigQuery, then repaired."""

    service = StubBigQueryService(
        dry_run_error=BadRequest("Unrecognized name: revenue"),
    )

    pipeline = build_pipeline(
        service=service,
        generator_llm=ScriptedLLMClient([GOOD_SQL]),
        repairer_llm=ScriptedLLMClient([GOOD_SQL, GOOD_SQL]),
    )

    result = pipeline.run(QUESTION)

    assert result["status"] == "error"
    assert result["stage"] == "dry_run"
    assert result["repair_attempts"] == 2

    stages = [
        repair["failure_stage"]
        for repair in result["repair_history"]  # type: ignore[union-attr]
    ]

    assert stages == ["dry_run", "dry_run"]


def test_execution_failures_are_not_repaired(
    stub_bigquery_service: StubBigQueryService,
) -> None:
    """A warehouse failure is not something rewriting SQL can fix."""

    service = StubBigQueryService(
        run_query_error=BadRequest("Resources exceeded"),
    )
    repairer_llm = ScriptedLLMClient([GOOD_SQL])

    pipeline = build_pipeline(
        service=service,
        generator_llm=ScriptedLLMClient([GOOD_SQL]),
        repairer_llm=repairer_llm,
    )

    result = pipeline.run(QUESTION)

    assert result["status"] == "error"
    assert result["stage"] == "execution"
    assert result["repair_attempts"] == 0
    assert repairer_llm.prompts == []


def test_schema_retrieval_failure_is_reported_cleanly(
    stub_bigquery_service: StubBigQueryService,
) -> None:
    pipeline = build_pipeline(
        service=stub_bigquery_service,
        generator_llm=ScriptedLLMClient([GOOD_SQL]),
        schema_provider=StubSchemaProvider(
            error=RuntimeError("dataset unavailable"),
        ),
    )

    result = pipeline.run(QUESTION)

    assert result["status"] == "error"
    assert result["stage"] == "schema_retrieval"
    assert result["error_type"] == "RuntimeError"


def test_a_dead_llm_is_a_structured_error_not_a_crash(
    stub_bigquery_service: StubBigQueryService,
) -> None:
    pipeline = build_pipeline(
        service=stub_bigquery_service,
        generator_llm=ScriptedLLMClient(
            [],
            error=LLMProviderError("Ollama is not running"),
        ),
    )

    result = pipeline.run(QUESTION)

    assert result["status"] == "error"
    assert result["stage"] == "sql_generation"
    assert result["error_type"] == "LLMProviderError"


def test_an_llm_failure_during_repair_is_reported(
    stub_bigquery_service: StubBigQueryService,
) -> None:
    pipeline = build_pipeline(
        service=stub_bigquery_service,
        generator_llm=ScriptedLLMClient([FOREIGN_TABLE_SQL]),
        repairer_llm=ScriptedLLMClient(
            [],
            error=LLMProviderError("Ollama died mid-repair"),
        ),
    )

    result = pipeline.run(QUESTION)

    assert result["status"] == "error"
    assert result["stage"] == "sql_repair"
    assert result["error_type"] == "LLMProviderError"


def test_negative_repair_budget_is_rejected(
    stub_bigquery_service: StubBigQueryService,
) -> None:
    try:
        build_pipeline(
            service=stub_bigquery_service,
            generator_llm=ScriptedLLMClient([GOOD_SQL]),
            max_repair_attempts=-1,
        )
    except ValueError as error:
        assert "cannot be negative" in str(error)
    else:  # pragma: no cover - the constructor must raise
        raise AssertionError("A negative repair budget must be rejected.")


def test_the_orchestrator_writes_its_own_run_record(
    stub_bigquery_service: StubBigQueryService,
    run_records: list[dict[str, object]],
) -> None:
    """README and PIPELINE.md claim both orchestrators log; prove it."""

    pipeline = build_pipeline(
        service=stub_bigquery_service,
        generator_llm=ScriptedLLMClient([GOOD_SQL]),
    )

    result = pipeline.run(QUESTION)

    question_records = [
        record
        for record in run_records
        if record["event"] == "question_to_sql"
    ]

    assert len(question_records) == 1

    record = question_records[0]

    assert record["status"] == "success"
    assert record["question"] == QUESTION
    assert record["repair_attempts"] == 0
    assert record["question_run_id"] == result["question_run_id"]

    # The execution pipeline's own record is still written too.
    assert any(
        record["event"] == "sql_execution" for record in run_records
    )


def test_run_metadata_is_stamped_on_every_result(
    stub_bigquery_service: StubBigQueryService,
) -> None:
    pipeline = build_pipeline(
        service=stub_bigquery_service,
        generator_llm=ScriptedLLMClient([GOOD_SQL]),
    )

    result = pipeline.run(QUESTION)

    assert isinstance(result["question_run_id"], str)
    assert isinstance(
        result["question_pipeline_duration_ms"],
        float,
    )
    assert result["llm_metadata"]["model_name"] == (  # type: ignore[index]
        "scripted-model"
    )
