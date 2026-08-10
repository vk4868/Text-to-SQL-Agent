from dataclasses import dataclass

from src.llm.base import (
    LLMClient,
    LLMResponse
)

from src.prompts.sql_generation import (
    build_sql_generation_prompt
)
from src.sql_text import clean_sql_output

@dataclass(frozen=True)
class SQLGenerationResult:
    """Represent one text to SQL generation attempt."""

    question: str
    sql: str
    raw_model_output: str
    llm_response: LLMResponse

class SQLGenerator:
    """Generate BigQuery SQL using an LLM and live schema"""

    def __init__(
        self,
        *,
        llm: LLMClient
    ) -> None:
        
        self.llm = llm
    def generate(
        self,
        *,
        question: str,
        schema_document: str,
    ) -> SQLGenerationResult:
        """ Generate SQL for one business Question."""

        prompt = build_sql_generation_prompt(
            question=question,
            schema_document=schema_document
        )
        llm_response = self.llm.generate_response(prompt)
        
        generated_sql = clean_sql_output(
            llm_response.text
        )

        return SQLGenerationResult(
            question = question.strip(),
            sql = generated_sql,
            raw_model_output = llm_response.text,
            llm_response = llm_response
        )
