from dataclasses import dataclass

from src.llm.base import (
    LLMClient,
    LLMResponse,
)
from src.prompts.sql_repair import (
    build_sql_repair_prompt,
)
from src.sql_generator import SQLGenerator


@dataclass(frozen=True)
class SQLRepairResult:
    """Represent one SQL repair attempt"""

    original_sql: str
    repaired_sql: str
    raw_model_output: str
    failure_stage: str
    error_message: str
    llm_response: LLMResponse

class SQLRepairer:
    """Repair failed bigquery SQL using an LLM."""

    def __init__(
        self,
        *,
        llm: LLMClient,
    ) -> None:
        self.llm = llm
    def repair(
        self,
        *,
        question: str,
        schema_document: str,
        failed_sql: str,
        error_message: str,
        failure_stage: str,
    ) -> SQLRepairResult:
        """Generate a corrected SQL Query"""
        prompt = build_sql_repair_prompt(
            question=question,
            schema_document=schema_document,
            failed_sql=failed_sql,
            error_message=error_message,
            failure_stage=failure_stage,
        )
        llm_response = self.llm.generate_response(
            prompt
        )
        repaired_sql = SQLGenerator._clean_sql_output(
            llm_response.text
        )
        return SQLRepairResult(
            original_sql=failed_sql.strip(),
            repaired_sql=repaired_sql,
            raw_model_output=llm_response.text,
            failure_stage=failure_stage,
            error_message=error_message,
            llm_response=llm_response
        )

    