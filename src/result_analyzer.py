from dataclasses import dataclass, field
from typing import Any

from src import config
from src.analysis_contract import (
    claim_bearing_text,
    validate_analysis_structure,
)
from src.analysis_guard import find_ungrounded_numbers
from src.llm.base import (
    LLMClient,
    LLMResponse,
)
from src.prompts.result_analysis import (
    build_result_analysis_prompt,
)

#: Returned verbatim when a query succeeds but matches nothing. Asking a small
#: model to write an analysis of zero rows is the single most reliable way to
#: get an invented one, so this path never reaches the LLM.
EMPTY_RESULT_ANALYSIS = """DIRECT ANSWER:
The query ran successfully but returned no rows, so there is no data to
answer this question.

KEY INSIGHTS:
- No records matched the filters used by this query.

SUPPORTING NUMBERS:
- Rows returned: 0

LIMITATIONS:
No conclusion can be drawn from an empty result. The filters may be too
narrow, or the dataset may not cover the requested period or entity.

SUGGESTED FOLLOW-UP:
Widen the filters — for example a broader date range or fewer conditions —
and check that the requested values exist in the dataset."""


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

    #: Deterministic checks on what the model wrote.
    contract_violations: list[str] = field(default_factory=list)
    ungrounded_numbers: list[str] = field(default_factory=list)

    #: True when the analysis was produced without calling the model.
    was_generated_deterministically: bool = False

    @property
    def is_grounded(self) -> bool:
        """Whether the analysis passed both deterministic checks."""

        return not self.contract_violations and not self.ungrounded_numbers


class ResultAnalyzer:
    """Convert structured SQL results into business insights"""

    def __init__(
        self,
        *,
        llm: LLMClient,
        max_analysis_rows: int | None = None,
    ) -> None:
        if max_analysis_rows is None:
            max_analysis_rows = config.MAX_ANALYSIS_ROWS

        if max_analysis_rows <= 0:
            raise ValueError(
                "max_analysis_rows must be greater than 0"
            )

        self.llm = llm
        self.max_analysis_rows = max_analysis_rows

    def analyze(
        self,
        *,
        question: str,
        sql: str,
        rows: list[dict[str, Any]],
        total_result_rows: int,
    ) -> ResultAnalysisResult:
        """Analyze a successful SQL Query result"""

        if total_result_rows < 0:
            raise ValueError(
                "total_result_rows must be non-negative"
            )

        if not rows:
            return self._empty_result(
                question=question,
                total_result_rows=total_result_rows,
            )

        rows_for_analysis = rows[: self.max_analysis_rows]

        rows_were_truncated = (
            total_result_rows > len(rows_for_analysis)
        )

        prompt = build_result_analysis_prompt(
            question=question,
            sql=sql,
            rows=rows_for_analysis,
            total_result_rows=total_result_rows,
            rows_were_truncated=rows_were_truncated,
        )

        llm_response = self.llm.generate_response(prompt)

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
            contract_violations=validate_analysis_structure(analysis),
            # Only the question grounds the prose. The SQL is deliberately
            # NOT passed: it is the same untrusted model output being
            # checked, so a model could otherwise launder a fabricated
            # figure by first writing it into its own WHERE clause.
            # Only the claim-bearing sections. LIMITATIONS and SUGGESTED
            # FOLLOW-UP are asked to discuss data outside the result, so
            # their figures are proposals rather than assertions.
            ungrounded_numbers=find_ungrounded_numbers(
                claim_bearing_text(analysis),
                rows_for_analysis,
                context=(question,),
            ),
        )

    def _empty_result(
        self,
        *,
        question: str,
        total_result_rows: int,
    ) -> ResultAnalysisResult:
        """Answer an empty result without consulting the model."""

        return ResultAnalysisResult(
            question=question.strip(),
            analysis=EMPTY_RESULT_ANALYSIS,
            raw_model_output="",
            rows_analyzed=0,
            total_result_rows=total_result_rows,
            rows_were_truncated=False,
            llm_response=LLMResponse(
                text="",
                model_name="deterministic-empty-result",
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                response_time_ms=0.0,
            ),
            contract_violations=[],
            ungrounded_numbers=[],
            was_generated_deterministically=True,
        )
