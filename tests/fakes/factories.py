"""Build fully-wired graph collaborators for tests.

InsightsGraphNodes requires five collaborators, all keyword-only with no
defaults. That invariant is correct — a half-wired node set is a bug — but it
means every test would otherwise repeat the same construction. This is the one
place that knows the wiring.
"""

from typing import Any

from src.graph.nodes import InsightsGraphNodes
from src.llm.scripted import ScriptedLLMClient
from src.result_analyzer import ResultAnalyzer
from src.schema_provider import SchemaProvider
from src.sql_execution_pipeline import SQLExecutionPipeline
from src.sql_generator import SQLGenerator
from src.sql_repairer import SQLRepairer
from tests.fakes import data
from tests.fakes.bigquery import FakeBigQueryService

WELL_FORMED_ANALYSIS = """DIRECT ANSWER:
Net sales rose steadily across the first quarter of 2025.

KEY INSIGHTS:
- March was the strongest month at 44005.00.
- February was the weakest month at 38910.20.

SUPPORTING NUMBERS:
- January: 41250.75
- March: 44005.00

LIMITATIONS:
None identified from the supplied result.

SUGGESTED FOLLOW-UP:
Break the same period down by product category."""


def make_nodes(
    *,
    bigquery_service: Any = None,
    llm: Any = None,
    generation_llm: Any = None,
    repair_llm: Any = None,
    analysis_llm: Any = None,
    max_repair_attempts: int = 2,
    relationships: list[str] | None = None,
) -> InsightsGraphNodes:
    """Wire a node set over fakes.

    A single ``llm`` drives all three roles, which mirrors production. Passing
    the per-role clients instead gives each role its own response queue, which
    is far easier to reason about when a test only cares about one of them.
    """

    reader = bigquery_service or FakeBigQueryService()

    shared = llm or ScriptedLLMClient([])

    schema_provider = SchemaProvider(
        bigquery_service=reader,
        relationships=(
            relationships
            if relationships is not None
            else list(data.RELATIONSHIPS)
        ),
    )

    return InsightsGraphNodes(
        schema_provider=schema_provider,
        sql_generator=SQLGenerator(llm=generation_llm or shared),
        sql_execution_pipeline=SQLExecutionPipeline(
            bigquery_service=reader
        ),
        sql_repairer=SQLRepairer(llm=repair_llm or shared),
        result_analyzer=ResultAnalyzer(llm=analysis_llm or shared),
        max_repair_attempts=max_repair_attempts,
    )


def make_insights_graph(**kwargs):
    """Build the full production graph over fakes."""

    from src.graph.builder import build_insights_graph

    return build_insights_graph(nodes=make_nodes(**kwargs))


def initial_state(question: str = "What were net sales by month?") -> dict:
    """Return the minimal input state a graph run needs."""

    return {"question": question}
