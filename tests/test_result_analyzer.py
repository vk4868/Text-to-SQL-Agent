"""Turning result rows back into a written business answer."""

import pytest

from src.result_analyzer import ResultAnalyzer
from tests.conftest import ScriptedLLMClient

QUESTION = "What were total net sales by month in 2025?"
SQL = "SELECT sale_date FROM `test-project.test_dataset.fact_sales`"
ANALYSIS = "DIRECT ANSWER:\nNet sales totalled 1,000."

ROWS = [
    {"sales_month": "2025-01", "total_sales": 400},
    {"sales_month": "2025-02", "total_sales": 600},
]


def analyze(
    llm: ScriptedLLMClient,
    *,
    rows: list[dict[str, object]] | None = None,
    total_result_rows: int | None = None,
    max_analysis_rows: int = 50,
):
    analyzer = ResultAnalyzer(
        llm=llm,
        max_analysis_rows=max_analysis_rows,
    )

    rows_to_use = ROWS if rows is None else rows

    return analyzer.analyze(
        question=QUESTION,
        sql=SQL,
        rows=rows_to_use,
        total_result_rows=(
            len(rows_to_use)
            if total_result_rows is None
            else total_result_rows
        ),
    )


def test_the_analysis_text_is_returned() -> None:
    result = analyze(ScriptedLLMClient([ANALYSIS]))

    assert result.analysis == ANALYSIS
    assert result.question == QUESTION
    assert result.rows_analyzed == 2
    assert result.rows_were_truncated is False


def test_rows_beyond_the_cap_are_not_sent_to_the_model() -> None:
    llm = ScriptedLLMClient([ANALYSIS])

    rows = [{"n": index} for index in range(100)]

    result = analyze(llm, rows=rows, max_analysis_rows=10)

    assert result.rows_analyzed == 10
    assert result.rows_were_truncated is True
    assert '"n": 10' not in llm.prompts[0]


def test_truncation_is_declared_in_the_prompt() -> None:
    llm = ScriptedLLMClient([ANALYSIS])

    analyze(
        llm,
        rows=ROWS,
        total_result_rows=500,
    )

    assert "ROWS WERE TRUNCATED:\n\nTrue" in llm.prompts[0]
    assert "TOTAL RESULT ROWS:\n\n500" in llm.prompts[0]


def test_an_empty_result_is_still_analysable() -> None:
    result = analyze(
        ScriptedLLMClient([ANALYSIS]),
        rows=[],
        total_result_rows=0,
    )

    assert result.rows_analyzed == 0
    assert result.rows_were_truncated is False


def test_an_empty_model_answer_is_rejected() -> None:
    with pytest.raises(ValueError, match="empty analysis"):
        analyze(ScriptedLLMClient(["   "]))


def test_a_non_positive_row_cap_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_analysis_rows"):
        ResultAnalyzer(
            llm=ScriptedLLMClient([ANALYSIS]),
            max_analysis_rows=0,
        )


def test_a_negative_total_row_count_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        analyze(
            ScriptedLLMClient([ANALYSIS]),
            total_result_rows=-1,
        )


def test_the_prompt_forbids_inventing_numbers() -> None:
    """The grounding rules are the reason the analysis is trustworthy."""

    llm = ScriptedLLMClient([ANALYSIS])

    analyze(llm)

    prompt = llm.prompts[0]

    assert "Do not invent, estimate, or assume missing values." in prompt
    assert "Do not claim causation" in prompt
    assert SQL in prompt
