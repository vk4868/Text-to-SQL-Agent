"""ResultAnalyzer: truncation, the zero-row short circuit, and the checks."""

from decimal import Decimal

import pytest

from src.llm.scripted import ScriptedLLMClient
from src.result_analyzer import (
    EMPTY_RESULT_ANALYSIS,
    ResultAnalyzer,
)
from tests.fakes.factories import WELL_FORMED_ANALYSIS

ROWS = [
    {"month": "2025-01", "total_net_sales": Decimal("41250.75")},
    {"month": "2025-02", "total_net_sales": Decimal("38910.20")},
    {"month": "2025-03", "total_net_sales": Decimal("44005.00")},
]

QUESTION = "What were net sales by month?"
SQL = "SELECT month, total_net_sales FROM `p.d.fact_sales`"


def analyze(llm, rows=ROWS, total=None):
    analyzer = ResultAnalyzer(llm=llm)
    return analyzer.analyze(
        question=QUESTION,
        sql=SQL,
        rows=rows,
        total_result_rows=len(rows) if total is None else total,
    )


class TestZeroRows:
    """An empty result is where a small model invents most confidently."""

    def test_no_llm_call_is_made(self):
        llm = ScriptedLLMClient([])

        result = analyze(llm, rows=[], total=0)

        assert llm.call_count == 0
        assert llm.prompts == []
        assert result.was_generated_deterministically is True

    def test_the_canned_answer_is_returned(self):
        result = analyze(ScriptedLLMClient([]), rows=[], total=0)

        assert result.analysis == EMPTY_RESULT_ANALYSIS
        assert "no rows" in result.analysis
        assert result.rows_analyzed == 0

    def test_the_canned_answer_satisfies_the_contract(self):
        result = analyze(ScriptedLLMClient([]), rows=[], total=0)

        assert result.contract_violations == []
        assert result.ungrounded_numbers == []
        assert result.is_grounded is True

    def test_zero_token_accounting(self):
        result = analyze(ScriptedLLMClient([]), rows=[], total=0)

        assert result.llm_response.total_tokens == 0
        assert result.llm_response.model_name == (
            "deterministic-empty-result"
        )


class TestGroundedAnalysis:
    def test_a_well_formed_analysis_passes_both_checks(self):
        result = analyze(ScriptedLLMClient([WELL_FORMED_ANALYSIS]))

        assert result.contract_violations == []
        assert result.ungrounded_numbers == []
        assert result.is_grounded is True
        assert result.was_generated_deterministically is False

    def test_an_invented_figure_is_reported(self):
        analysis = WELL_FORMED_ANALYSIS + "\nAnd a total of $99,123.45."

        result = analyze(ScriptedLLMClient([analysis]))

        assert result.ungrounded_numbers == ["$99,123.45"]
        assert result.is_grounded is False

    def test_a_missing_section_is_reported(self):
        analysis = WELL_FORMED_ANALYSIS.replace(
            "LIMITATIONS:\nNone identified from the supplied result.", ""
        )

        result = analyze(ScriptedLLMClient([analysis]))

        assert any(
            "LIMITATIONS" in violation
            for violation in result.contract_violations
        )
        assert result.is_grounded is False

    def test_the_analysis_is_still_returned_when_checks_fail(self):
        """Violations are reported, not fatal."""

        analysis = "Sales were $99,123.45."

        result = analyze(ScriptedLLMClient([analysis]))

        assert result.analysis == analysis
        assert result.contract_violations
        assert result.ungrounded_numbers


class TestTruncation:
    def test_rows_are_capped_at_max_analysis_rows(self):
        rows = [{"n": i, "v": i * 1.5} for i in range(100)]
        analyzer = ResultAnalyzer(
            llm=ScriptedLLMClient([WELL_FORMED_ANALYSIS]),
            max_analysis_rows=10,
        )

        result = analyzer.analyze(
            question=QUESTION,
            sql=SQL,
            rows=rows,
            total_result_rows=100,
        )

        assert result.rows_analyzed == 10
        assert result.rows_were_truncated is True

    def test_truncation_flag_is_false_when_all_rows_are_seen(self):
        result = analyze(ScriptedLLMClient([WELL_FORMED_ANALYSIS]))

        assert result.rows_were_truncated is False

    def test_truncation_is_declared_in_the_prompt(self):
        rows = [{"n": i} for i in range(100)]
        llm = ScriptedLLMClient([WELL_FORMED_ANALYSIS])
        analyzer = ResultAnalyzer(llm=llm, max_analysis_rows=5)

        analyzer.analyze(
            question=QUESTION, sql=SQL, rows=rows, total_result_rows=100
        )

        assert "ROWS WERE TRUNCATED:\n\nTrue" in llm.prompts[0]


class TestValidation:
    def test_negative_total_rows_is_rejected(self):
        with pytest.raises(ValueError, match="non-negative"):
            analyze(ScriptedLLMClient([]), total=-1)

    def test_non_positive_max_analysis_rows_is_rejected(self):
        with pytest.raises(ValueError, match="greater than 0"):
            ResultAnalyzer(llm=ScriptedLLMClient([]), max_analysis_rows=0)

    def test_an_empty_model_response_is_rejected(self):
        with pytest.raises(ValueError, match="empty analysis"):
            analyze(ScriptedLLMClient(["   "]))
