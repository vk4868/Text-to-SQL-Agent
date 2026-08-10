"""Cleaning raw model output into runnable SQL.

Both the generator and the repairer share this function, so both are
covered by these cases.
"""

import pytest

from src.sql_generator import SQLGenerator
from src.sql_repairer import SQLRepairer
from src.sql_text import clean_sql_output
from tests.conftest import ScriptedLLMClient

SQL = "SELECT sale_id FROM `test-project.test_dataset.fact_sales`"


@pytest.mark.parametrize(
    "fence",
    ["```sql", "```bigquery", "```", "```SQL"],
)
def test_code_fences_are_removed(fence: str) -> None:
    assert clean_sql_output(f"{fence}\n{SQL}\n```") == SQL


def test_unfenced_sql_is_returned_unchanged() -> None:
    assert clean_sql_output(SQL) == SQL


def test_surrounding_whitespace_is_stripped() -> None:
    assert clean_sql_output(f"\n\n  {SQL}  \n\n") == SQL


def test_an_unclosed_fence_is_still_handled() -> None:
    assert clean_sql_output(f"```sql\n{SQL}") == SQL


def test_internal_newlines_are_preserved() -> None:
    multiline = "SELECT\n  sale_id\nFROM t"

    assert clean_sql_output(f"```sql\n{multiline}\n```") == multiline


@pytest.mark.parametrize("model_output", ["", "   ", "\n\t"])
def test_empty_model_output_raises(model_output: str) -> None:
    with pytest.raises(ValueError, match="empty SQL response"):
        clean_sql_output(model_output)


def test_output_that_is_only_a_fence_raises() -> None:
    with pytest.raises(ValueError, match="No SQL remained"):
        clean_sql_output("```sql\n```")


def test_the_generator_uses_the_shared_cleaner() -> None:
    generator = SQLGenerator(
        llm=ScriptedLLMClient([f"```sql\n{SQL}\n```"]),
    )

    result = generator.generate(
        question="How many sales were there?",
        schema_document="DATASET: test-project.test_dataset",
    )

    assert result.sql == SQL
    assert result.raw_model_output == f"```sql\n{SQL}\n```"
    assert result.question == "How many sales were there?"


def test_the_repairer_uses_the_shared_cleaner() -> None:
    repairer = SQLRepairer(
        llm=ScriptedLLMClient([f"```bigquery\n{SQL}\n```"]),
    )

    result = repairer.repair(
        question="How many sales were there?",
        schema_document="DATASET: test-project.test_dataset",
        failed_sql="SELECT revenue FROM t",
        error_message="Unrecognized name: revenue",
        failure_stage="dry_run",
    )

    assert result.repaired_sql == SQL
    assert result.failure_stage == "dry_run"
    assert result.error_message == "Unrecognized name: revenue"


def test_an_empty_generation_is_a_value_error() -> None:
    generator = SQLGenerator(llm=ScriptedLLMClient(["   "]))

    with pytest.raises(ValueError):
        generator.generate(
            question="How many sales were there?",
            schema_document="DATASET: test-project.test_dataset",
        )
