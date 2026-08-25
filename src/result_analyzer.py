from dataclasses import dataclass
from typing import Any

from src.config import MAX_ANALYSIS_ROWS
from src.llm.base import (
    LLMClient,
    LLMResponse,
)
from src.prompts.result_analysis import (
    build_result_analysis_prompt,
)

@dataclass(frozen=True)
class ResultAnalysisResult:
    """Represent one LLM based SQL result analysis"""
    question: str
    analysis: str
    raw_model_output: str
    rows_analyzed: int
    total_result_rows: int
    rows_were_truncated: bool
    llm_response: LLMResponse

class ResultAnalyzer:
    """Convert structured SQL results into business insights"""

    def __init__(
        self,
        *,
        llm:LLMClient,
        max_analysis_rows: int = MAX_ANALYSIS_ROWS,
    ) -> None:
        if max_analysis_rows <=0:
            raise ValueError(
                "max_analysis_rows must be greater than 0"
            )
        self.llm = llm
        self.max_analysis_rows = max_analysis_rows

    def analyze(
        self,
        *,
        question: str,
        sql:str,
        rows:list[dict[str,Any]],
        total_result_rows: int,
    ) -> ResultAnalysisResult:
        """Analyze a successful SQL Query result"""

        if total_result_rows < 0:
            raise ValueError(
                "total_result_rows must be non-negative"
            )
        rows_for_analysis = rows[
            :self.max_analysis_rows
        ]

        rows_were_truncated = (
            total_result_rows
            > len(rows_for_analysis)
        )

        prompt = build_result_analysis_prompt(
            question=question,
            sql=sql,
            rows=rows_for_analysis,
            total_result_rows=total_result_rows,
            rows_were_truncated=rows_were_truncated,
        )

        llm_response = self.llm.generate_response(
            prompt
        )

        analysis = llm_response.text.strip()

        if not analysis:
            raise ValueError(
                "The model returned an empty analysis."
            )

        return ResultAnalysisResult(
            question=question.strip(),
            analysis=analysis,
            raw_model_output=llm_response.text,
            rows_analyzed=len(rows_for_analysis),
            total_result_rows=total_result_rows,
            rows_were_truncated=rows_were_truncated,
            llm_response=llm_response,
        )
        
