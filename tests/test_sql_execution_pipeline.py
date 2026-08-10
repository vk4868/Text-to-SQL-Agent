"""The six-stage execution pipeline, exercised with a stub warehouse."""

import logging

import pytest
from google.api_core.exceptions import BadRequest

from src.exceptions import QueryExecutionTimeoutError
from src.sql_execution_pipeline import SQLExecutionPipeline
from tests.conftest import SAMPLE_SQL, StubBigQueryService

EXPECTED_STAGE_ORDER = [
    "read_only_validation",
    "table_access_validation",
    "result_limit_enforcement",
    "dry_run",
    "cost_check",
    "execution",
]


def build_pipeline(
    service: StubBigQueryService,
    logger: logging.Logger,
    **kwargs: object,
) -> SQLExecutionPipeline:
    return SQLExecutionPipeline(
        bigquery_service=service,
        logger=logger,
        **kwargs,  # type: ignore[arg-type]
    )


def test_valid_query_succeeds_end_to_end(
    stub_bigquery_service: StubBigQueryService,
    silent_logger: logging.Logger,
) -> None:
    pipeline = build_pipeline(stub_bigquery_service, silent_logger)

    result = pipeline.execute(SAMPLE_SQL)

    assert result["status"] == "success"
    assert result["stage"] == "execution"
    assert result["row_count"] == 1
    assert result["referenced_tables"] == [
        "test-project.test_dataset.fact_sales"
    ]
    assert "run_id" in result
    assert isinstance(result["duration_ms"], float)


def test_rejected_at_stage_one_for_a_delete(
    stub_bigquery_service: StubBigQueryService,
    silent_logger: logging.Logger,
) -> None:
    pipeline = build_pipeline(stub_bigquery_service, silent_logger)

    result = pipeline.execute(
        "DELETE FROM `test-project.test_dataset.fact_sales` WHERE TRUE"
    )

    assert result["status"] == "rejected"
    assert result["stage"] == "validation"
    assert result["error_type"] == "SQLValidationError"
    # Nothing reached BigQuery.
    assert stub_bigquery_service.dry_run_calls == []
    assert stub_bigquery_service.run_query_calls == []


def test_rejected_at_stage_two_for_a_foreign_table(
    stub_bigquery_service: StubBigQueryService,
    silent_logger: logging.Logger,
) -> None:
    pipeline = build_pipeline(stub_bigquery_service, silent_logger)

    result = pipeline.execute(
        "SELECT * FROM `bigquery-public-data.thelook_ecommerce.orders`"
    )

    assert result["status"] == "rejected"
    assert result["stage"] == "table_access"
    assert result["error_type"] == "TableAccessValidationError"
    assert stub_bigquery_service.dry_run_calls == []


def test_rejected_at_stage_three_for_a_parameterised_limit(
    stub_bigquery_service: StubBigQueryService,
    silent_logger: logging.Logger,
) -> None:
    pipeline = build_pipeline(stub_bigquery_service, silent_logger)

    result = pipeline.execute(f"{SAMPLE_SQL} LIMIT @row_limit")

    assert result["status"] == "rejected"
    assert result["stage"] == "result_limit"
    assert result["error_type"] == "ResultLimitValidationError"
    assert stub_bigquery_service.dry_run_calls == []


def test_error_at_stage_four_when_bigquery_rejects_the_sql(
    silent_logger: logging.Logger,
) -> None:
    service = StubBigQueryService(
        dry_run_error=BadRequest("Unrecognized name: revenue"),
    )
    pipeline = build_pipeline(service, silent_logger)

    result = pipeline.execute(SAMPLE_SQL)

    assert result["status"] == "error"
    assert result["stage"] == "dry_run"
    assert "Unrecognized name" in str(result["message"])
    # A failed dry run costs nothing and never executes.
    assert service.run_query_calls == []


def test_rejected_at_stage_five_when_the_query_is_too_expensive(
    silent_logger: logging.Logger,
) -> None:
    service = StubBigQueryService(dry_run_bytes=500_000_000)
    pipeline = build_pipeline(
        service,
        silent_logger,
        max_query_bytes=100_000_000,
    )

    result = pipeline.execute(SAMPLE_SQL)

    assert result["status"] == "rejected"
    assert result["stage"] == "cost_check"
    assert result["error_type"] == "QueryCostLimitExceeded"
    assert result["estimated_bytes_processed"] == 500_000_000
    assert service.run_query_calls == []


def test_error_at_stage_six_on_timeout(
    silent_logger: logging.Logger,
) -> None:
    service = StubBigQueryService(
        run_query_error=QueryExecutionTimeoutError(
            job_id="job-123",
            timeout_seconds=30,
            cancel_requested=True,
        ),
    )
    pipeline = build_pipeline(service, silent_logger)

    result = pipeline.execute(SAMPLE_SQL)

    assert result["status"] == "error"
    assert result["stage"] == "execution_timeout"
    assert result["job_id"] == "job-123"
    assert result["cancel_requested"] is True


def test_error_at_stage_six_on_a_bigquery_failure(
    silent_logger: logging.Logger,
) -> None:
    service = StubBigQueryService(
        run_query_error=BadRequest("Resources exceeded"),
    )
    pipeline = build_pipeline(service, silent_logger)

    result = pipeline.execute(SAMPLE_SQL)

    assert result["status"] == "error"
    assert result["stage"] == "execution"
    assert "Resources exceeded" in str(result["message"])


def test_row_limit_is_injected_into_the_executed_sql(
    stub_bigquery_service: StubBigQueryService,
    silent_logger: logging.Logger,
) -> None:
    pipeline = build_pipeline(
        stub_bigquery_service,
        silent_logger,
        max_result_rows=25,
    )

    result = pipeline.execute(SAMPLE_SQL)

    assert result["limit_was_modified"] is True
    assert result["result_row_limit"] == 25
    assert "LIMIT 25" in str(result["executed_sql"])
    assert "LIMIT 25" in stub_bigquery_service.run_query_calls[0]["sql"]


def test_execution_carries_the_cost_row_and_time_caps(
    stub_bigquery_service: StubBigQueryService,
    silent_logger: logging.Logger,
) -> None:
    pipeline = build_pipeline(
        stub_bigquery_service,
        silent_logger,
        max_query_bytes=1_234,
        max_result_rows=7,
        query_timeout_seconds=11,
    )

    pipeline.execute(SAMPLE_SQL)

    call = stub_bigquery_service.run_query_calls[0]

    assert call["maximum_bytes_billed"] == 1_234
    assert call["max_result_rows"] == 7
    assert call["query_timeout_seconds"] == 11


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_query_bytes", 0),
        ("max_result_rows", 0),
        ("query_timeout_seconds", 0),
        ("max_query_bytes", -1),
    ],
)
def test_non_positive_caps_are_rejected_at_construction(
    stub_bigquery_service: StubBigQueryService,
    field: str,
    value: int,
) -> None:
    with pytest.raises(ValueError):
        SQLExecutionPipeline(
            bigquery_service=stub_bigquery_service,
            **{field: value},  # type: ignore[arg-type]
        )


def test_successful_run_records_every_stage_timing(
    stub_bigquery_service: StubBigQueryService,
    silent_logger: logging.Logger,
) -> None:
    pipeline = build_pipeline(stub_bigquery_service, silent_logger)

    result = pipeline.execute(SAMPLE_SQL)

    timings = result["stage_timings_ms"]

    assert isinstance(timings, dict)
    assert list(timings) == EXPECTED_STAGE_ORDER
    assert all(value >= 0 for value in timings.values())


def test_early_rejection_records_only_the_stages_that_ran(
    stub_bigquery_service: StubBigQueryService,
    silent_logger: logging.Logger,
) -> None:
    pipeline = build_pipeline(stub_bigquery_service, silent_logger)

    result = pipeline.execute("DROP TABLE `test-project.test_dataset.x`")

    timings = result["stage_timings_ms"]

    assert isinstance(timings, dict)
    assert list(timings) == ["read_only_validation"]


def test_total_duration_covers_the_stage_timings(
    stub_bigquery_service: StubBigQueryService,
    silent_logger: logging.Logger,
) -> None:
    pipeline = build_pipeline(stub_bigquery_service, silent_logger)

    result = pipeline.execute(SAMPLE_SQL)

    timings = result["stage_timings_ms"]

    assert isinstance(timings, dict)
    assert result["duration_ms"] >= sum(timings.values()) - 0.01


def test_every_outcome_writes_one_run_record(
    stub_bigquery_service: StubBigQueryService,
    silent_logger: logging.Logger,
    run_records: list[dict[str, object]],
) -> None:
    pipeline = build_pipeline(stub_bigquery_service, silent_logger)

    pipeline.execute(SAMPLE_SQL)
    pipeline.execute("DROP TABLE `test-project.test_dataset.x`")

    assert len(run_records) == 2
    assert [record["status"] for record in run_records] == [
        "success",
        "rejected",
    ]
    assert all(
        record["event"] == "sql_execution" for record in run_records
    )
    assert all("stage_timings_ms" in record for record in run_records)


def test_pipeline_never_raises_for_bad_input(
    stub_bigquery_service: StubBigQueryService,
    silent_logger: logging.Logger,
) -> None:
    """Callers always get a structured dict, never an exception."""

    pipeline = build_pipeline(stub_bigquery_service, silent_logger)

    for sql in ["", "SELEKT ***", "SELECT 1; DROP TABLE t"]:
        result = pipeline.execute(sql)

        assert result["status"] == "rejected"
        assert "message" in result
