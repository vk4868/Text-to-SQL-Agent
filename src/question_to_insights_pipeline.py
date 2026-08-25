from time import perf_counter
from uuid import uuid4

from src.exceptions import LLMProviderError
from src.question_to_sql_pipeline import (
    QuestionToSQLPipeline
)

from src.result_analyzer import ResultAnalyzer

class QuestionToInsightsPipeline:
    """ Convert a business question into validated SQL and insights."""

    def __init__(
        self,
        *,
        question_to_sql_pipeline: QuestionToSQLPipeline,
        result_analyzer: ResultAnalyzer,
    ) -> None:
        self.question_to_sql_pipeline = (
            question_to_sql_pipeline)
        self.result_analyzer = result_analyzer
    
    def run(
        self,
        question: str,
    ) -> dict[str,object]:
        """Run the complete question-to-insights-workflow"""
        run_id = str(uuid4())
        started_at = perf_counter()

        question_result = (
            self.question_to_sql_pipeline.run(
                question
            )
        )
        if question_result["status"] != "success":
            return self._finalize_result(
                run_id=run_id,
                started_at=started_at,
                result = {
                    "status": question_result["status"],
                    "stage": question_result["stage"],
                    "question": question.strip(),
                    "question_to_sql": question_result
                },
            )
        sql_execution = question_result.get(
            "sql_execution"
        )
        if not isinstance(sql_execution, dict):
            return self._finalize_result(
                run_id=run_id,
                started_at=started_at,
                result={
                    "status": "error",
                    "stage": "result_validation",
                    "error_type": "InvalidExecutionResult",
                    "message": (
                        "SQL execution result was not "
                        "returned as a dictionary."
                    ),
                    "question": question.strip(),
                    "question_to_sql": question_result,
                },
            )

        rows = sql_execution.get("rows")

        if not isinstance(rows, list):
            return self._finalize_result(
                run_id=run_id,
                started_at=started_at,
                result={
                    "status": "error",
                    "stage": "result_validation",
                    "error_type": "InvalidRowsResult",
                    "message": (
                        "SQL execution rows were not "
                        "returned as a list."
                    ),
                    "question": question.strip(),
                    "question_to_sql": question_result,
                },
            )

        total_result_rows = int(
            sql_execution.get(
                "total_result_rows",
                len(rows),
            )
        )

        try:
            analysis_result = (
                self.result_analyzer.analyze(
                    question=question,
                    sql=str(
                        question_result[
                            "final_sql"
                        ]
                    ),
                    rows=rows,
                    total_result_rows=(
                        total_result_rows
                    ),
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
                    "stage": "result_analysis",
                    "error_type": type(error).__name__,
                    "message": str(error),
                    "question": question.strip(),
                    "question_to_sql": question_result,
                },
            )

        return self._finalize_result(
            run_id=run_id,
            started_at=started_at,
            result={
                "status": "success",
                "stage": "complete",
                "question": question.strip(),
                "generated_sql": question_result[
                    "generated_sql"
                ],
                "final_sql": question_result[
                "final_sql"
                ],
                "repair_attempts": question_result[
                "repair_attempts"
                ],

                "rows": rows,
                "business_analysis": (
                    analysis_result.analysis
                ),
                "question_to_sql": question_result,
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
                    "model_name": (
                        analysis_result
                        .llm_response
                        .model_name
                    ),
                    "input_tokens": (
                        analysis_result
                        .llm_response
                        .input_tokens
                    ),
                    "output_tokens": (
                        analysis_result
                        .llm_response
                        .output_tokens
                    ),
                    "total_tokens": (
                        analysis_result
                        .llm_response
                        .total_tokens
                    ),
                    "response_time_ms": (
                        analysis_result
                        .llm_response
                        .response_time_ms
                    ),
                },
            },
        )
    @staticmethod
    def _finalize_result(
        *, run_id: str,
        started_at: float,
        result: dict[str,object],
    ) -> dict[str,object]:
        """Add metadata for the complete insights workflow"""

        duration_ms = round(
            (perf_counter() - started_at) * 1000, 2
        )
        return {
            "insights_run_id": run_id,
            "insights_pipeline_duration_ms": duration_ms,
            **result
        }
