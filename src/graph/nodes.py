"""The graph's nodes and routers.

Two conventions hold throughout:

1. A node returns only the delta it produced. node_trace, repair_history,
   llm_calls and sql_execution_run_ids are accumulators (see AgentState), so
   returning a whole list would duplicate everything already in it.
2. A node never raises for an expected failure. It records error_stage and
   error_message, and a router decides what happens next. This mirrors the
   execution pipeline's structured-result convention.
"""

from dataclasses import asdict
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

from google.api_core.exceptions import GoogleAPIError

from src import config
from src.exceptions import LLMProviderError
from src.graph.instrumentation import timed_node
from src.graph.state import AgentState
from src.llm.base import LLMResponse
from src.observability import (
    configure_application_logger,
    write_jsonl_record,
)
from src.repair_policy import should_repair
from src.result_analyzer import ResultAnalyzer
from src.run_record import build_graph_run_record
from src.schema_provider import SchemaProvider
from src.sql_execution_pipeline import SQLExecutionPipeline
from src.sql_generator import SQLGenerator
from src.sql_repairer import SQLRepairer


def _llm_call_record(
    purpose: str,
    llm_response: LLMResponse,
) -> dict[str, Any]:
    """Summarise one LLM call for the run record.

    The response text is dropped: it is already stored as generated_sql or
    business_analysis, and duplicating it would multiply the record size.
    """

    call = asdict(llm_response)
    call.pop("text", None)
    return {"purpose": purpose, **call}


class InsightsGraphNodes:
    """Provide langgraph nodes for the word to insights workflow"""

    def __init__(
        self,
        *,
        schema_provider: SchemaProvider,
        sql_generator: SQLGenerator,
        sql_execution_pipeline: SQLExecutionPipeline,
        sql_repairer: SQLRepairer,
        result_analyzer: ResultAnalyzer,
        max_repair_attempts: int | None = None,
        logger=None,
    ) -> None:
        if max_repair_attempts is None:
            max_repair_attempts = config.MAX_SQL_REPAIR_ATTEMPTS

        if max_repair_attempts < 0:
            raise ValueError(
                "max_repair_attempts cannot be negative"
            )

        self.schema_provider = schema_provider
        self.sql_generator = sql_generator
        self.sql_execution_pipeline = sql_execution_pipeline
        self.sql_repairer = sql_repairer
        self.result_analyzer = result_analyzer
        self.max_repair_attempts = max_repair_attempts
        self.logger = logger or configure_application_logger()

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def initialize_run_node(
        self,
        state: AgentState,
    ) -> AgentState:
        """Stamp run identity and reset per-run state.

        The accumulators are not initialised here. LangGraph applies
        operator.add to whatever a node returns, so returning [] would be a
        no-op, and the reducer already treats a missing key as empty.

        The question is validated here so a junk request costs neither an LLM
        call nor a BigQuery job.
        """

        base: AgentState = {
            "graph_run_id": str(uuid4()),
            "run_started_at": perf_counter(),
            "status": "running",
            "terminal_stage": "",
            "error_stage": "",
            "error_message": "",
            "repair_attempts": 0,
        }

        question = str(state.get("question", "") or "").strip()

        if not question:
            return {
                **base,
                "error_stage": "question_validation",
                "error_message": "Question cannot be empty.",
            }

        return {**base, "question": question}

    @staticmethod
    def route_after_initialization(
        state: AgentState,
    ) -> Literal["schema", "end"]:
        """Stop immediately when the question is unusable."""

        if state.get("error_stage") == "question_validation":
            return "end"
        return "schema"

    def finalize_run_node(
        self,
        state: AgentState,
    ) -> AgentState:
        """Close out the run: classify it, log it, and record it.

        Every terminal path in the insights graph routes here, so this is the
        single exit — the graph-level counterpart of the execution pipeline's
        _finalize_result. It must therefore tolerate states where execution,
        SQL, or analysis never happened.
        """

        started_at = state.get("run_started_at")
        total_duration_ms = (
            round((perf_counter() - started_at) * 1000, 2)
            if isinstance(started_at, (int, float))
            else None
        )

        status, terminal_stage = self._classify_outcome(state)

        completed_state: AgentState = {
            **state,
            "status": status,
            "terminal_stage": terminal_stage,
        }

        record = build_graph_run_record(
            completed_state,
            total_duration_ms=total_duration_ms,
        )

        self._log_run(record)
        write_jsonl_record(record)

        return {
            "status": status,
            "terminal_stage": terminal_stage,
            "run_record": record,
        }

    @staticmethod
    def _classify_outcome(state: AgentState) -> tuple[str, str]:
        """Return (status, terminal_stage) for a finished run.

        A guardrail rejection is reported as "rejected" rather than "error":
        the system worked as designed and refused the query, which is a
        materially different outcome from something breaking.
        """

        error_stage = str(state.get("error_stage", "") or "")

        if not error_stage:
            if state.get("business_analysis"):
                return "success", "complete"
            if state.get("execution_result"):
                return "success", "execution"
            return "error", "incomplete"

        execution_result = state.get("execution_result") or {}

        if (
            execution_result.get("stage") == error_stage
            and execution_result.get("status") == "rejected"
        ):
            return "rejected", error_stage

        return "error", error_stage

    def _log_run(self, record: dict[str, Any]) -> None:
        """Emit one human-readable line per run, at a level matching status."""

        outcome = record.get("outcome", {})
        status = outcome.get("status")

        message = (
            f"Graph run finished | "
            f"graph_run_id={record.get('graph_run_id')} | "
            f"status={status} | "
            f"stage={outcome.get('terminal_stage')} | "
            f"repairs={record.get('sql', {}).get('repair_attempts')} | "
            f"duration_ms="
            f"{record.get('runtime', {}).get('total_duration_ms')} | "
            f"tokens="
            f"{record.get('tokens', {}).get('totals', {}).get('total_tokens')}"
        )

        if status == "success":
            self.logger.info(message)
        elif status == "rejected":
            self.logger.warning(message)
        else:
            self.logger.error(message)

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    @timed_node("get_schema")
    def get_schema_node(
        self,
        state: AgentState,
    ) -> AgentState:
        """Retrieve the live bigquery schema"""

        try:
            schema_document = (
                self.schema_provider.get_schema_document()
            )
        except GoogleAPIError as error:
            return {
                "error_stage": "schema_retrieval",
                "error_message": str(error),
            }

        if not schema_document.strip():
            return {
                "error_stage": "schema_retrieval",
                "error_message": (
                    "The schema document came back empty."
                ),
            }

        return {
            "schema_document": schema_document,
            "error_stage": "",
            "error_message": "",
        }

    @staticmethod
    def route_after_schema(
        state: AgentState,
    ) -> Literal["generate", "end"]:
        """Continue only when schema retrieval succeeded"""

        if state.get("error_stage") == "schema_retrieval":
            return "end"
        if not state.get("schema_document"):
            return "end"
        return "generate"

    # ------------------------------------------------------------------
    # SQL generation
    # ------------------------------------------------------------------

    @timed_node("generate_sql")
    def generate_sql_node(
        self,
        state: AgentState,
    ) -> AgentState:
        """Generate Bigquery SQL from the question and schema"""

        question = state.get("question")
        schema_document = state.get("schema_document")

        if not question:
            return {
                "error_stage": "sql_generation",
                "error_message": (
                    "Question is missing from graph state"
                ),
            }

        if not schema_document:
            return {
                "error_stage": "sql_generation",
                "error_message": (
                    "Schema document is missing from graph state"
                ),
            }

        try:
            generation_result = self.sql_generator.generate(
                question=question,
                schema_document=schema_document,
            )
        except (LLMProviderError, ValueError) as error:
            return {
                "error_stage": "sql_generation",
                "error_message": str(error),
            }

        return {
            "generated_sql": generation_result.sql,
            "current_sql": generation_result.sql,
            "llm_calls": [
                _llm_call_record(
                    "sql_generation",
                    generation_result.llm_response,
                )
            ],
            "error_stage": "",
            "error_message": "",
        }

    @staticmethod
    def route_after_generation(
        state: AgentState,
    ) -> Literal["execute", "end"]:
        """Continue only when SQL generation succeeded"""

        if state.get("error_stage") == "sql_generation":
            return "end"
        if not state.get("current_sql"):
            return "end"
        return "execute"

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    @timed_node("execute_sql")
    def execute_sql_node(
        self,
        state: AgentState,
    ) -> AgentState:
        """Safely execute the current SQL query."""

        current_sql = state.get("current_sql")

        if not current_sql:
            return {
                "error_stage": "sql_execution",
                "error_message": (
                    "No current SQL is available for execution."
                ),
            }

        execution_result = self.sql_execution_pipeline.execute(
            current_sql
        )

        # Links this run to the execution pipeline's own JSONL records.
        run_ids = (
            [str(execution_result["run_id"])]
            if execution_result.get("run_id")
            else []
        )

        if execution_result["status"] != "success":
            return {
                "execution_result": execution_result,
                "sql_execution_run_ids": run_ids,
                "error_stage": str(
                    execution_result.get("stage", "sql_execution")
                ),
                "error_message": str(
                    execution_result.get(
                        "message", "SQL execution failed."
                    )
                ),
            }

        return {
            "execution_result": execution_result,
            "sql_execution_run_ids": run_ids,
            "final_sql": current_sql,
            "error_stage": "",
            "error_message": "",
        }

    def route_after_execution(
        self,
        state: AgentState,
    ) -> Literal["analyze", "repair", "end"]:
        """Choose whether to finish, analyse, or repair failed SQL"""

        execution_result = state.get("execution_result")

        if not isinstance(execution_result, dict):
            return "end"

        if execution_result.get("status") == "success":
            return "analyze"

        failure_stage = str(
            execution_result.get("stage", "unknown")
        )

        if should_repair(
            failure_stage=failure_stage,
            repairs_used=int(state.get("repair_attempts", 0)),
            max_repair_attempts=self.max_repair_attempts,
        ):
            return "repair"

        return "end"

    # ------------------------------------------------------------------
    # Repair
    # ------------------------------------------------------------------

    @timed_node("repair_sql")
    def repair_sql_node(
        self,
        state: AgentState,
    ) -> AgentState:
        """Repair the current SQL using the previous execution error."""

        question = state.get("question")
        schema_document = state.get("schema_document")
        current_sql = state.get("current_sql")
        execution_result = state.get("execution_result")

        if not question:
            return {
                "error_stage": "sql_repair",
                "error_message": (
                    "Question is missing from graph state."
                ),
            }

        if not schema_document:
            return {
                "error_stage": "sql_repair",
                "error_message": (
                    "Schema document is missing from graph state."
                ),
            }

        if not current_sql:
            return {
                "error_stage": "sql_repair",
                "error_message": (
                    "Current SQL is missing from graph state."
                ),
            }

        if not isinstance(execution_result, dict):
            return {
                "error_stage": "sql_repair",
                "error_message": (
                    "Execution result is missing from graph state."
                ),
            }

        failure_stage = str(
            execution_result.get("stage", "unknown")
        )
        error_message = str(
            execution_result.get("message", "SQL execution failed.")
        )

        try:
            repair_result = self.sql_repairer.repair(
                question=question,
                schema_document=schema_document,
                failed_sql=current_sql,
                error_message=error_message,
                failure_stage=failure_stage,
            )
        except (LLMProviderError, ValueError) as error:
            return {
                "error_stage": "sql_repair",
                "error_message": str(error),
            }

        next_attempt = int(state.get("repair_attempts", 0)) + 1

        return {
            "current_sql": repair_result.repaired_sql,
            "repair_attempts": next_attempt,
            # Accumulator: only the new entry, never the whole history.
            "repair_history": [
                {
                    "repair_attempt": next_attempt,
                    "failure_stage": failure_stage,
                    "error_message": error_message,
                    "failed_sql": current_sql,
                    "repaired_sql": repair_result.repaired_sql,
                }
            ],
            "llm_calls": [
                _llm_call_record(
                    "sql_repair",
                    repair_result.llm_response,
                )
            ],
            "error_stage": "",
            "error_message": "",
        }

    @staticmethod
    def route_after_repair(
        state: AgentState,
    ) -> Literal["execute", "end"]:
        """Choose whether repaired SQL should be executed"""

        if state.get("error_stage") == "sql_repair":
            return "end"
        if not state.get("current_sql"):
            return "end"
        return "execute"

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    @timed_node("analyze_result")
    def analyze_result_node(
        self,
        state: AgentState,
    ) -> AgentState:
        """Convert a successful SQL result into business insights"""

        question = state.get("question")
        final_sql = state.get("final_sql")
        execution_result = state.get("execution_result")

        if not question:
            return {
                "error_stage": "result_analysis",
                "error_message": (
                    "Question is missing from graph state"
                ),
            }

        if not final_sql:
            return {
                "error_stage": "result_analysis",
                "error_message": (
                    "Final SQL is missing from graph state"
                ),
            }

        if not isinstance(execution_result, dict):
            return {
                "error_stage": "result_analysis",
                "error_message": (
                    "Execution result is missing from graph state"
                ),
            }

        if execution_result.get("status") != "success":
            return {
                "error_stage": "result_analysis",
                "error_message": (
                    "Cannot analyse an unsuccessful SQL execution"
                ),
            }

        rows = execution_result.get("rows", [])

        if not isinstance(rows, list):
            return {
                "error_stage": "result_analysis",
                "error_message": "SQL result rows are not a list.",
            }

        total_result_rows = int(
            execution_result.get("total_result_rows", len(rows))
        )

        try:
            analysis_result = self.result_analyzer.analyze(
                question=question,
                sql=final_sql,
                rows=rows,
                total_result_rows=total_result_rows,
            )
        except (LLMProviderError, ValueError) as error:
            return {
                "error_stage": "result_analysis",
                "error_message": str(error),
            }

        return {
            "business_analysis": analysis_result.analysis,
            "analysis_metadata": {
                "rows_analyzed": analysis_result.rows_analyzed,
                "total_result_rows": analysis_result.total_result_rows,
                "rows_were_truncated": (
                    analysis_result.rows_were_truncated
                ),
            },
            "llm_calls": [
                _llm_call_record(
                    "result_analysis",
                    analysis_result.llm_response,
                )
            ],
            "error_stage": "",
            "error_message": "",
        }
