from dataclasses import dataclass

from src.llm.base import (
    LLMClient,
    LLMResponse
)

from src.prompts.sql_generation import (
    build_sql_generation_prompt
)

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
        
        generated_sql = self._clean_sql_output(
            llm_response.text
        )

        return SQLGenerationResult(
            question = question.strip(),
            sql = generated_sql,
            raw_model_output = llm_response.text,
            llm_response = llm_response
        )
    
    @staticmethod
    def _clean_sql_output(
        model_output: str,
    ) -> str:
        """Remove an optional Markdown code fence from SQL output."""

        cleaned_output = model_output.strip()

        if not cleaned_output:
            raise ValueError(
                "The model returned an empty SQL response."
            )

        lines = cleaned_output.splitlines()

        if lines and lines[0].strip().lower() in {
            "```sql",
            "```bigquery",
            "```",
        }:
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        cleaned_sql = "\n".join(lines).strip()

        if not cleaned_sql:
            raise ValueError(
                "No SQL remained after cleaning the model output."
            )

        return cleaned_sql