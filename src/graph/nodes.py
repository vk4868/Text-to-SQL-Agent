from google.api_core.exceptions import GoogleAPIError
from dataclasses import asdict

from src.config import MAX_SQL_REPAIR_ATTEMPTS
from src.exceptions import LLMProviderError
from src.sql_repairer import SQLRepairer

from src.graph.state import AgentState
from src.schema_provider import SchemaProvider
from src.sql_generator import SQLGenerator
from src.sql_execution_pipeline import SQLExecutionPipeline
from src.result_analyzer import ResultAnalyzer
from typing import Literal

from time import perf_counter
from uuid import uuid4
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
        max_repair_attempts: int = MAX_SQL_REPAIR_ATTEMPTS
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
        self.result_analyzer = result_analyzer


    def initialize_run_node(
        self,state:AgentState,
    ) -> AgentState:
        """Initialize metadata for one graph execution"""
        return {
            "graph_run_id": str(uuid4()),
            "node_trace": [],
            "error_stage": "",
            "error_message":"",
        }
    @staticmethod
    def _record_node_execution(
        *,
        state: AgentState,
        node_name: str,
        started_at: float,
        update:AgentState,
    ) -> AgentState:
        """Add node timing information to a state update"""

        duration_ms = round(
            (perf_counter() - started_at) * 1000,
            2
        
        )

        node_trace = list(
            state.get(
                "node_trace",
                [],
            )
        )

        node_trace.append(
            {
                "node": node_name,
                "duration_ms": duration_ms
            }
        )
        return {
            **update,
            "node_trace": node_trace
        }


    def get_schema_node(
        self,
        state:AgentState,

    ) -> AgentState:
        """Retrieve the live bigquery schema"""
        started_at = perf_counter()
        try:
            schema_document = (
                self.schema_provider.get_schema_document()
            )
        except GoogleAPIError as error:
            return {
                "error_stage":"schema_retrieval",
                "error_message": str(error)
            }
        return {
            "schema_document": schema_document,
            "error_stage": "",
            "error_message":"",
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


    def generate_sql_node(
        self,
        state: AgentState,
    ) -> AgentState:
        """Generate Bigquery SQL from the question and schema"""
        started_at = perf_counter()
        question = state.get("question")
        schema_document = state.get("schema_document")

        if not question:
            return {
                "error_stage":"sql_generation",
                "error_message": (
                    "Question is missing from graph state"
                )
            }
        if not schema_document:
            return{
                "error_stage":"sql_generation",
                "error_message": (
                    "Schema document is missing from graph state"
                )
            }
        try:
            generation_result = self.sql_generator.generate(
                question=question,
                schema_document=schema_document
            )
        except (
            LLMProviderError,
            ValueError
        ) as error:
            return {
                "error_stage":"sql_generation",
                "error_message": str(error),
            }
        
        return self._record_node_execution(
            state=state,
            node_name="generate_sql",
            started_at=started_at,
            update={
                "generated_sql": generation_result.sql,
                "current_sql": generation_result.sql,
                "error_stage": "",
                "error_message": "",
            },
        )
    
    @staticmethod
    def route_after_generation(
        state:AgentState,
    ) -> Literal["execute","end"]:
        """Continue only when SQL generation succeeded"""

        if state.get("error_stage") == "sql_generation":
            return "end"
        if not state.get("current_sql"):
            return "end"
        return "execute"


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

        execution_result = (
            self.sql_execution_pipeline.execute(
                current_sql
            )
        )

        if execution_result["status"] != "success":
            return {
                "execution_result": execution_result,
                "error_stage": str(
                execution_result.get(
                    "stage",
                    "sql_execution",
                )
            ),
            "error_message": str(
                execution_result.get(
                    "message",
                    "SQL execution failed.",
                )
            ),
        }

        return {
            "execution_result": execution_result,
            "final_sql": current_sql,
            "error_stage": "",
            "error_message": "",
        }

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
            execution_result.get(
                "stage",
                "unknown",
            )
        )

        error_message = str(
            execution_result.get(
                "message",
                "SQL execution failed.",
            )
        )

        repair_attempts = state.get(
                "repair_attempts",
                0,
            )

        try:
            repair_result = self.sql_repairer.repair(
                question=question,
                schema_document=schema_document,
                failed_sql=current_sql,
                error_message=error_message,
                failure_stage=failure_stage,
            )

        except (
            LLMProviderError,
            ValueError,
        ) as error:
            return {
                "error_stage": "sql_repair",
                "error_message": str(error),
            }

        next_repair_attempt = repair_attempts + 1

        repair_history = list(
            state.get(
                "repair_history",
                [],
            )
        )

        repair_history.append(
            {
                "repair_attempt": next_repair_attempt,
                "failure_stage": failure_stage,
                "error_message": error_message,
                "failed_sql": current_sql,
                "repaired_sql": repair_result.repaired_sql,
                "llm_metadata": asdict(
                    repair_result.llm_response
                ),
            }
        )

        return {
            "current_sql": repair_result.repaired_sql,
            "repair_attempts": next_repair_attempt,
            "repair_history": repair_history,
            "error_stage": "",
            "error_message": "",
        }


    def analyze_result_node(
        self,
        state: AgentState,
    ) -> AgentState:
        """Convert a successful SQL result into business insights"""

        question = state.get("question")
        final_sql = state.get("final_sql")
        execution_result = state.get(
            "execution_result"
        )

        if not question:
            return {
                "error_stage":"result_analysis",
                "error_message":(
                "Question is missing from graph state"
                )
            }
        if not final_sql:
            return {
                "error_stage": "result_analysis",
                "error_message": (
                    "Final SQL is missing from Graph state"
                )
            }
        if not isinstance(execution_result, dict):
            return {
                "error_stage":"result_analysis",
                "error_message": (
                    "Execution result is missing from graph state"
                )
            }
        if execution_result.get("status") !="success":
            return {
                "error_stage":"result_analysis",
                "error_message": (
                    "Cannot analyse an unsuccesful SQL execution"
                )
            }

        rows = execution_result.get(
            "rows",
            [],
        )

        if not isinstance(rows, list):
            return {
                "error_stage": "result_analysis",
                "error_message": (
                    "SQL result rows are not a list."
                ),
            }

        total_result_rows = int(
            execution_result.get(
                "total_result_rows",
                len(rows),
            )
        )

        try:
            analysis_result = (
                self.result_analyzer.analyze(
                    question=question,
                    sql=final_sql,
                    rows=rows,
                    total_result_rows=total_result_rows,
                )
            )

        except (
            LLMProviderError,
            ValueError,
        ) as error:
            return {
                "error_stage": "result_analysis",
                "error_message": str(error),
            }

        return {
            "business_analysis": (
                analysis_result.analysis
            ),
            "analysis_metadata": {
                "rows_analyzed": (
                    analysis_result.rows_analyzed
                ),
                "total_result_rows": (
                    analysis_result.total_result_rows
                ),
                "rows_were_truncated": (
                    analysis_result.rows_were_truncated
                ),
                "llm_response": asdict(
                    analysis_result.llm_response
                ),
            },
            "error_stage": "",
            "error_message": "",
        }


    def route_after_execution(
        self,
        state:AgentState,
    ) -> str:
        """Choose whether to finish or repair failed SQL"""

        execution_result = state.get(
            "execution_result"
        )
        if not isinstance(execution_result,dict):
            return "end"
        if execution_result.get("status") =="success":
            return "analyze"
        
        failure_stage = str(
            execution_result.get(
                "stage",
                "unknown",
            )
        )

        repairable_stages = {
            "validation",
            "table_access",
            "dry_run"
        }

        if failure_stage not in repairable_stages:
            return "end"
        repair_attempts = int(
            state.get(
                "repair_attempts",
                0
            )
        )

        if repair_attempts >=self.max_repair_attempts:
            return "end"
        return "repair"

    def route_after_repair(
        self,
        state:AgentState
    ) -> str:
        """Choose whether repaired SQL should be executed"""

        if state.get("error_stage") == "sql_repair":
            return "end"
        if not state.get("current_sql"):
            return "end"

        return "execute"