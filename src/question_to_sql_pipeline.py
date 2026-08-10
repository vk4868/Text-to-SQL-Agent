import logging
from dataclasses import asdict
from time import perf_counter
from uuid import uuid4

from src.config import MAX_SQL_REPAIR_ATTEMPTS

from src.exceptions import LLMProviderError
from src.observability import (
    configure_application_logger,
    write_jsonl_record,
)
from src.schema_provider import SchemaProvider
from src.sql_execution_pipeline import (
    SQLExecutionPipeline,
)
from src.sql_generator import SQLGenerator
from src.sql_repairer import SQLRepairer


class QuestionToSQLPipeline:
    """ Convert a business question into SQL and execute it safely"""

    REPAIRABLE_STAGES = {
        "validation",
        "table_access",
        "dry_run",
    }

    def __init__(
        self,
        *,
        schema_provider: SchemaProvider,
        sql_generator: SQLGenerator,
        sql_execution_pipeline: SQLExecutionPipeline,
        sql_repairer: SQLRepairer,
        max_repair_attempts: int = MAX_SQL_REPAIR_ATTEMPTS,
        logger: logging.Logger | None = None,
    ) -> None:
        if max_repair_attempts < 0:
            raise ValueError(
                "max_repair_attempts cannot be negative"
            )

        self.schema_provider = schema_provider
        self.sql_generator = sql_generator
        self.sql_execution_pipeline = sql_execution_pipeline
        self.sql_repairer = sql_repairer
        self.max_repair_attempts = max_repair_attempts
        self.logger = logger or configure_application_logger()

    def run(
        self,
        question: str,
    ) -> dict[str, object]:
        """ Generate and safely execute SQL for one question"""

        run_id = str(uuid4())
        started_at = perf_counter()

        self.logger.info(
            "Question pipeline started | "
            f"question_run_id={run_id}"
        )

        cleaned_question = question.strip()

        if not cleaned_question:
            return self._finalize_result(
                run_id=run_id,
                started_at=started_at,
                result={
                    "status": "rejected",
                    "stage": "question_validation",
                    "error_type": "QuestionValidationError",
                    "message": "Question cannot be empty",
                },
            )

        try:
            schema_document = (
                self.schema_provider.get_schema_document()
            )
        except Exception as error:
            return self._finalize_result(
                run_id=run_id,
                started_at=started_at,
                result={
                    "status": "error",
                    "stage": "schema_retrieval",
                    "error_type": type(error).__name__,
                    "message": str(error),
                    "question": cleaned_question,
                },
            )

        try:
            generation_result = (
                self.sql_generator.generate(
                    question=cleaned_question,
                    schema_document=schema_document,
                )
            )
        except (
            LLMProviderError,
            ValueError,
        ) as error:
            return self._finalize_result(
                run_id=run_id,
                started_at=started_at,
                result={
                    "status": "error",
                    "stage": "sql_generation",
                    "error_type": type(error).__name__,
                    "message": str(error),
                    "question": cleaned_question,
                },
            )

        initial_sql = generation_result.sql
        current_sql = initial_sql

        repair_history: list[dict[str, object]] = []

        execution_result: dict[str, object] | None = None

        for execution_attempt in range(
            self.max_repair_attempts + 1
        ):
            execution_result = (
                self.sql_execution_pipeline.execute(
                    current_sql
                )
            )

            if execution_result["status"] == "success":
                break

            failure_stage = str(
                execution_result.get(
                    "stage",
                    "unknown",
                )
            )

            if failure_stage not in self.REPAIRABLE_STAGES:
                break

            repairs_already_used = len(repair_history)

            if repairs_already_used >= self.max_repair_attempts:
                break

            error_message = str(
                execution_result.get(
                    "message",
                    "SQL execution failed",
                )
            )

            try:
                repair_result = self.sql_repairer.repair(
                    question=cleaned_question,
                    schema_document=schema_document,
                    failed_sql=current_sql,
                    error_message=error_message,
                    failure_stage=failure_stage,
                )
            except (
                LLMProviderError,
                ValueError,
            ) as error:
                return self._finalize_result(
                    run_id=run_id,
                    started_at=started_at,
                    result={
                        "status": "error",
                        "stage": "sql_repair",
                        "error_type": type(error).__name__,
                        "message": str(error),
                        "question": cleaned_question,
                        "generated_sql": initial_sql,
                        "repair_history": repair_history,
                    },
                )

            repair_history.append(
                {
                    "repair_attempt": (
                        repairs_already_used + 1
                    ),
                    "failure_stage": failure_stage,
                    "error_message": error_message,
                    "failed_sql": current_sql,
                    "repaired_sql": repair_result.repaired_sql,
                    "llm_metadata": asdict(
                        repair_result.llm_response
                    ),
                }
            )

            current_sql = repair_result.repaired_sql

        if execution_result is None:
            return self._finalize_result(
                run_id=run_id,
                started_at=started_at,
                result={
                    "status": "error",
                    "stage": "sql_repair",
                    "error_type": "MissingExecutionResult",
                    "message": (
                        "No SQL execution result was produced"
                    ),
                    "question": cleaned_question,
                },
            )

        if execution_result["status"] != "success":
            return self._finalize_result(
                run_id=run_id,
                started_at=started_at,
                result={
                    "status": execution_result["status"],
                    "stage": execution_result["stage"],
                    "question": cleaned_question,
                    "generated_sql": initial_sql,
                    "final_sql": current_sql,
                    "repair_attempts": len(
                        repair_history
                    ),
                    "repair_history": repair_history,
                    "raw_model_output": (
                        generation_result.raw_model_output
                    ),
                    "llm_metadata": asdict(
                        generation_result.llm_response
                    ),
                    "sql_execution": execution_result,
                },
            )

        return self._finalize_result(
            run_id=run_id,
            started_at=started_at,
            result={
                "status": "success",
                "stage": "execution",
                "question": cleaned_question,
                "generated_sql": initial_sql,
                "final_sql": current_sql,
                "repair_attempts": len(
                    repair_history
                ),
                "repair_history": repair_history,
                "raw_model_output": (
                    generation_result.raw_model_output
                ),
                "llm_metadata": asdict(
                    generation_result.llm_response
                ),
                "sql_execution": execution_result,
            },
        )

    def _finalize_result(
        self,
        *,
        run_id: str,
        started_at: float,
        result: dict[str, object],
    ) -> dict[str, object]:
        """Add run metadata, write logs, and return the final result."""

        duration_ms = round((perf_counter() - started_at) * 1000, 2)

        complete_result: dict[str, object] = {
            "question_run_id": run_id,
            "question_pipeline_duration_ms": duration_ms,
            **result,
        }

        status = str(complete_result.get("status", "unknown"))
        stage = str(complete_result.get("stage", "unknown"))

        log_message = (
            "Question pipeline finished | "
            f"question_run_id={run_id} | "
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
                "event": "question_to_sql",
                "question_run_id": run_id,
                "status": status,
                "stage": stage,
                "duration_ms": duration_ms,
                "question": complete_result.get("question"),
                "repair_attempts": complete_result.get(
                    "repair_attempts",
                    0,
                ),
                "error_type": complete_result.get("error_type"),
                "message": complete_result.get("message"),
                "generated_sql": complete_result.get(
                    "generated_sql"
                ),
                "final_sql": complete_result.get("final_sql"),
            }
        )

        return complete_result
