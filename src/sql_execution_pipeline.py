from typing import cast

import logging
from time import perf_counter
from uuid import uuid4

from src.observability import (
    configure_application_logger,
    write_jsonl_record,
)

from google.api_core.exceptions import GoogleAPIError

from src import config
from src.interfaces import BigQueryReader
from src.exceptions import QueryExecutionTimeoutError
from src.sql_validator import (
    enforce_result_limit,
    validate_read_only_sql,
    validate_table_access,
)


class SQLExecutionPipeline:
    """Validate, cost-check, and execute read-only BigQuery SQL."""

    def __init__(
        self,
        *,
        bigquery_service: BigQueryReader,
        max_query_bytes: int | None = None,
        max_result_rows: int | None = None,
        query_timeout_seconds: int | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        # Resolved at call time, not captured as import-time defaults, so the
        # caps stay overridable by configuration and by tests.
        max_query_bytes = (
            config.MAX_QUERY_BYTES
            if max_query_bytes is None
            else max_query_bytes
        )
        max_result_rows = (
            config.MAX_RESULT_ROWS
            if max_result_rows is None
            else max_result_rows
        )
        query_timeout_seconds = (
            config.QUERY_TIMEOUT_SECONDS
            if query_timeout_seconds is None
            else query_timeout_seconds
        )

        if max_query_bytes <= 0:
            raise ValueError(
                "max_query_bytes must be greater than zero."
            )

        if max_result_rows <= 0:
            raise ValueError(
                "max_result_rows must be greater than zero."
            )

        if query_timeout_seconds <= 0:
            raise ValueError(
                "query_timeout_seconds must be greater than zero."
            )

        self.bigquery_service = bigquery_service
        self.max_query_bytes = max_query_bytes
        self.max_result_rows = max_result_rows
        self.query_timeout_seconds = query_timeout_seconds
        self.logger = logger or configure_application_logger()

    
    def _finalize_result(
        self,
        *,
        run_id: str,
        started_at: float,
        stage_timings_ms: dict[str,float],
        result: dict[str, object],
    ) -> dict[str, object]:
        """Add run metadata, write logs, and return the final result."""

        duration_ms = round(
            (perf_counter() - started_at) * 1000,
            2,
        )

        complete_result = {
            "run_id": run_id,
            "duration_ms": duration_ms,
            "stage_timings_ms": stage_timings_ms,
            **result,
        }

        status = str(
            complete_result.get("status", "unknown")
        )

        stage = str(
            complete_result.get("stage", "unknown")
        )

        log_message = (
            "SQL pipeline finished | "
            f"run_id={run_id} | "
            f"status={status} | "
            f"stage={stage} | "
            f"duration_ms={duration_ms}"
        )

        if status == "success":
            self.logger.info(log_message)

        elif status == "rejected":
            self.logger.warning(log_message)

        else:
            self.logger.error(log_message)

        write_jsonl_record(
            {
                "event": "sql_execution",
                "run_id": run_id,
                "status": status,
                "stage": stage,
                "duration_ms": duration_ms,
                "stage_timings_ms": stage_timings_ms,
                "job_id": complete_result.get("job_id"),
                "error_type": complete_result.get("error_type"),
                "message": complete_result.get("message"),
                "referenced_tables": complete_result.get(
                    "referenced_tables",
                    [],
                ),
                "row_count": complete_result.get("row_count"),
                "total_result_rows": complete_result.get(
                    "total_result_rows"
                ),
                "estimated_bytes_processed": complete_result.get(
                    "estimated_bytes_processed"
                ),
                "total_bytes_processed": complete_result.get(
                    "total_bytes_processed"
                ),
                "total_bytes_billed": complete_result.get(
                    "total_bytes_billed"
                ),
                "cache_hit": complete_result.get("cache_hit"),
                "result_row_limit": complete_result.get(
                    "result_row_limit"
                ),
                "limit_was_modified": complete_result.get(
                    "limit_was_modified"
                ),
                "validated_sql": complete_result.get(
                    "validated_sql"
                ),
                "executed_sql": complete_result.get(
                    "executed_sql"
                ),
            }
        )

        return complete_result

    def execute(self, sql: str) -> dict[str, object]:
        """Run SQL through every validation and execution stage."""
        run_id = str(uuid4())
        started_at = perf_counter()
        stage_timings_ms: dict[str,float] = {}

        self.logger.info(
            "SQL pipeline started | "
            f"run_id={run_id}"
        )

        stage_started = perf_counter()

        validation_result = validate_read_only_sql(sql)

        stage_timings_ms["read_only_validation"] = round(
            (perf_counter() - stage_started) * 1000,
            2,
        )

        if (
            not validation_result.is_valid
            or validation_result.normalized_sql is None
        ):
            return self._finalize_result(
                run_id=run_id,
                started_at=started_at,
                stage_timings_ms=stage_timings_ms,
                result={
                    "status": "rejected",
                    "stage": "validation",
                    "error_type": "SQLValidationError",
                    "message": validation_result.message,
                },
            )

        validated_sql = validation_result.normalized_sql

        stage_started = perf_counter()
        allowed_table_names = set(
            self.bigquery_service.list_table_names()
        )

        table_access_result = validate_table_access(
            validated_sql,
            allowed_project_id=self.bigquery_service.project_id,
            allowed_dataset_id=self.bigquery_service.dataset_id,
            allowed_table_names=allowed_table_names,
        )

        stage_timings_ms["table_access_validation"] = round(
            (perf_counter() - stage_started) * 1000, 2,
        )

        if not table_access_result.is_valid:
            return self._finalize_result(
                run_id=run_id,
                started_at=started_at,
                stage_timings_ms=stage_timings_ms,
                result={
                    "status": "rejected",
                    "stage": "table_access",
                    "error_type": "TableAccessValidationError",
                    "message": table_access_result.message,
                    "validated_sql": validated_sql,
                },
            )

        referenced_tables = list(
            table_access_result.referenced_tables
        )

        stage_started = perf_counter()

        limit_result = enforce_result_limit(
            validated_sql,
            max_rows=self.max_result_rows,
        )

        stage_timings_ms["result_limit_enforcement"] = round(
            (perf_counter() - stage_started) * 1000, 2,
        )

        if (
            not limit_result.is_valid
            or limit_result.limited_sql is None
        ):
            return self._finalize_result(
                run_id=run_id,
                started_at=started_at,
                stage_timings_ms=stage_timings_ms,
                result={
                    "status": "rejected",
                    "stage": "result_limit",
                    "error_type": "ResultLimitValidationError",
                    "message": limit_result.message,
                    "validated_sql": validated_sql,
                    "referenced_tables": referenced_tables,
                },
            )

        executed_sql = limit_result.limited_sql

        stage_started = perf_counter()

        try:
            dry_run_result = (
                self.bigquery_service.dry_run_query(
                    executed_sql
                )
            )

        except GoogleAPIError as error:
            return self._finalize_result(
                run_id=run_id,
                started_at=started_at,
                stage_timings_ms=stage_timings_ms,
                result={
                    "status": "error",
                    "stage": "dry_run",
                    "error_type": type(error).__name__,
                    "message": str(error),
                    "validated_sql": validated_sql,
                    "executed_sql": executed_sql,
                    "referenced_tables": referenced_tables,
                },
            )
        finally:
            stage_timings_ms["dry_run"] = round(
                (perf_counter() - stage_started) * 1000, 2,
            )

        stage_started = perf_counter()

        estimated_bytes = cast(
            int, dry_run_result["estimated_bytes_processed"]
        )
        exceeds_cost_limit = (
            estimated_bytes > self.max_query_bytes
        )

        stage_timings_ms["cost_check"] = round(
            (perf_counter() - stage_started) * 1000, 2,
        )

        if exceeds_cost_limit:
            return self._finalize_result(
                run_id=run_id,
                started_at=started_at,
                stage_timings_ms=stage_timings_ms,
                result={
                    "status": "rejected",
                    "stage": "cost_check",
                    "error_type": "QueryCostLimitExceeded",
                    "message": (
                        "Query exceeds the configured processing "
                        "limit. "
                        f"Estimated bytes: {estimated_bytes}. "
                        f"Allowed bytes: {self.max_query_bytes}."
                    ),
                    "validated_sql": validated_sql,
                    "executed_sql": executed_sql,
                    "referenced_tables": referenced_tables,
                    "estimated_bytes_processed": estimated_bytes,
                    "maximum_query_bytes": self.max_query_bytes,
                },
            )

        stage_started = perf_counter()

        try:
            query_result = self.bigquery_service.run_query(
                executed_sql,
                maximum_bytes_billed=self.max_query_bytes,
                max_result_rows=self.max_result_rows,
                query_timeout_seconds=(
                    self.query_timeout_seconds
                ),
            )

        except QueryExecutionTimeoutError as error:
            return self._finalize_result(
                run_id=run_id,
                started_at=started_at,
                stage_timings_ms=stage_timings_ms,
                result={
                    "status": "error",
                    "stage": "execution_timeout",
                    "error_type": type(error).__name__,
                    "message": str(error),
                    "job_id": error.job_id,
                    "timeout_seconds": error.timeout_seconds,
                    "cancel_requested": error.cancel_requested,
                    "validated_sql": validated_sql,
                    "executed_sql": executed_sql,
                    "referenced_tables": referenced_tables,
                    "estimated_bytes_processed": estimated_bytes,
                },
            )

        except GoogleAPIError as error:
            return self._finalize_result(
                run_id=run_id,
                started_at=started_at,
                stage_timings_ms=stage_timings_ms,
                result={
                    "status": "error",
                    "stage": "execution",
                    "error_type": type(error).__name__,
                    "message": str(error),
                    "validated_sql": validated_sql,
                    "executed_sql": executed_sql,
                    "referenced_tables": referenced_tables,
                    "estimated_bytes_processed": estimated_bytes,
                    "maximum_query_bytes": self.max_query_bytes,
                },
            )
        finally:
            stage_timings_ms["execution"] = round(
                (perf_counter() - stage_started) * 1000, 2,
            )

        return self._finalize_result(
            run_id=run_id,
            started_at=started_at,
            stage_timings_ms=stage_timings_ms,
            result={
                "status": "success",
                "stage": "execution",
                "validation_message": validation_result.message,
                "table_access_message": (
                    table_access_result.message
                ),
                "result_limit_message": limit_result.message,
                "validated_sql": validated_sql,
                "executed_sql": executed_sql,
                "referenced_tables": referenced_tables,
                "result_row_limit": limit_result.effective_limit,
                "limit_was_modified": limit_result.was_modified,
                "estimated_bytes_processed": estimated_bytes,
                "maximum_query_bytes": self.max_query_bytes,
                "query_timeout_seconds": (
                    self.query_timeout_seconds
                ),
                **query_result,
            },
        )