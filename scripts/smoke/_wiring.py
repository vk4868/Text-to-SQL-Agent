"""Build live collaborators for the smoke scripts.

InsightsGraphNodes requires all five collaborators, keyword-only and with no
defaults. Six smoke scripts each tried to supply a subset and every one of
them raised TypeError before reaching the code it meant to exercise. This is
the one place that knows the full live wiring.
"""

from src.bigquery_service import BigQueryService
from src.graph.nodes import InsightsGraphNodes
from src.llm.ollama_client import OllamaGemmaClient
from src.result_analyzer import ResultAnalyzer
from src.schema_config import RELATIONSHIPS
from src.schema_provider import SchemaProvider
from src.sql_execution_pipeline import SQLExecutionPipeline
from src.sql_generator import SQLGenerator
from src.sql_repairer import SQLRepairer


def build_live_nodes(**overrides) -> InsightsGraphNodes:
    """Return a fully-wired node set backed by live BigQuery and Ollama."""

    bigquery_service = overrides.pop(
        "bigquery_service", None
    ) or BigQueryService()
    llm = overrides.pop("llm", None) or OllamaGemmaClient()

    return InsightsGraphNodes(
        schema_provider=SchemaProvider(
            bigquery_service=bigquery_service,
            relationships=RELATIONSHIPS,
        ),
        sql_generator=SQLGenerator(llm=llm),
        sql_execution_pipeline=SQLExecutionPipeline(
            bigquery_service=bigquery_service,
        ),
        sql_repairer=SQLRepairer(llm=llm),
        result_analyzer=ResultAnalyzer(llm=llm),
        **overrides,
    )
