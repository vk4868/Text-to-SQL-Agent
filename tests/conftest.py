"""Shared fixtures and test doubles.

Every double here is deliberately dependency-free: the whole suite runs
with no Google Cloud credentials, no BigQuery access, and no LLM server.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from src import (
    question_to_insights_pipeline as insights_module,
)
from src import question_to_sql_pipeline as question_module
from src import sql_execution_pipeline as execution_module
from src.llm.base import LLMResponse

ALLOWED_PROJECT_ID = "test-project"
ALLOWED_DATASET_ID = "test_dataset"
ALLOWED_TABLE_NAMES = (
    "dim_customers",
    "dim_products",
    "fact_sales",
)

SAMPLE_SQL = (
    "SELECT sale_id FROM "
    "`test-project.test_dataset.fact_sales`"
)


class StubBigQueryService:
    """A BigQueryService-shaped double that never touches the network.

    Behaviour is configured per test: how many bytes a dry run reports,
    what rows execution returns, and which errors (if any) each stage
    should raise.
    """

    def __init__(
        self,
        *,
        project_id: str = ALLOWED_PROJECT_ID,
        dataset_id: str = ALLOWED_DATASET_ID,
        table_names: tuple[str, ...] = ALLOWED_TABLE_NAMES,
        dry_run_bytes: int = 1_000,
        rows: list[dict[str, Any]] | None = None,
        dry_run_error: Exception | None = None,
        run_query_error: Exception | None = None,
    ) -> None:
        self.project_id = project_id
        self.dataset_id = dataset_id
        self.location = "australia-southeast1"
        self.table_names = list(table_names)
        self.dry_run_bytes = dry_run_bytes
        self.rows = rows if rows is not None else [{"sale_id": "S1"}]
        self.dry_run_error = dry_run_error
        self.run_query_error = run_query_error

        # Recorded so tests can assert what actually reached BigQuery.
        self.dry_run_calls: list[str] = []
        self.run_query_calls: list[dict[str, Any]] = []
        self.list_table_names_calls = 0

    @property
    def dataset_path(self) -> str:
        return f"{self.project_id}.{self.dataset_id}"

    def list_table_names(self) -> list[str]:
        self.list_table_names_calls += 1
        return sorted(self.table_names)

    def dry_run_query(self, sql: str) -> dict[str, object]:
        self.dry_run_calls.append(sql)

        if self.dry_run_error is not None:
            raise self.dry_run_error

        return {
            "estimated_bytes_processed": self.dry_run_bytes,
            "statement_type": "SELECT",
        }

    def run_query(
        self,
        sql: str,
        maximum_bytes_billed: int | None = None,
        max_result_rows: int | None = None,
        query_timeout_seconds: int | None = None,
    ) -> dict[str, object]:
        self.run_query_calls.append(
            {
                "sql": sql,
                "maximum_bytes_billed": maximum_bytes_billed,
                "max_result_rows": max_result_rows,
                "query_timeout_seconds": query_timeout_seconds,
            }
        )

        if self.run_query_error is not None:
            raise self.run_query_error

        return {
            "rows": list(self.rows),
            "row_count": len(self.rows),
            "total_result_rows": len(self.rows),
            "result_truncated_by_client": False,
            "job_id": "stub-job-1",
            "statement_type": "SELECT",
            "total_bytes_processed": self.dry_run_bytes,
            "total_bytes_billed": self.dry_run_bytes,
            "cache_hit": False,
        }


class ScriptedLLMClient:
    """An LLMClient double that replays a fixed list of responses."""

    def __init__(
        self,
        responses: list[str],
        *,
        model_name: str = "scripted-model",
        error: Exception | None = None,
    ) -> None:
        self.responses = list(responses)
        self.model_name = model_name
        self.error = error
        self.prompts: list[str] = []

    def generate_response(self, prompt: str) -> LLMResponse:
        self.prompts.append(prompt)

        if self.error is not None:
            raise self.error

        if not self.responses:
            raise AssertionError(
                "ScriptedLLMClient ran out of scripted responses."
            )

        text = self.responses.pop(0)

        return LLMResponse(
            text=text,
            model_name=self.model_name,
            input_tokens=10,
            output_tokens=20,
            total_tokens=30,
            response_time_ms=1.0,
        )


class StubSchemaProvider:
    """Returns a fixed schema document without touching BigQuery."""

    def __init__(
        self,
        document: str = "DATASET: test-project.test_dataset",
        error: Exception | None = None,
    ) -> None:
        self.document = document
        self.error = error

    def get_schema_document(self) -> str:
        if self.error is not None:
            raise self.error

        return self.document


@pytest.fixture
def stub_bigquery_service() -> StubBigQueryService:
    """A healthy BigQuery double: dry run cheap, execution succeeds."""

    return StubBigQueryService()


@pytest.fixture
def silent_logger() -> logging.Logger:
    """A logger that writes nowhere, so tests create no log files."""

    logger = logging.getLogger("tests.silent")
    logger.handlers = [logging.NullHandler()]
    logger.propagate = False

    return logger


@pytest.fixture(autouse=True)
def run_records(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture JSONL run records instead of writing them to disk.

    Autouse so no test can accidentally create `logs/runs.jsonl`. Tests
    that care about observability request the fixture by name and assert
    on the captured records.
    """

    records: list[dict[str, Any]] = []

    def capture(
        record: dict[str, Any],
        file_path: str | None = None,
    ) -> None:
        records.append(record)

    for module in (
        execution_module,
        question_module,
        insights_module,
    ):
        monkeypatch.setattr(module, "write_jsonl_record", capture)

    return records


@pytest.fixture(autouse=True)
def no_log_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop pipelines building file-backed loggers during tests."""

    logger = logging.getLogger("tests.autouse")
    logger.handlers = [logging.NullHandler()]
    logger.propagate = False

    for module in (
        execution_module,
        question_module,
        insights_module,
    ):
        monkeypatch.setattr(
            module,
            "configure_application_logger",
            lambda *args, **kwargs: logger,
        )
