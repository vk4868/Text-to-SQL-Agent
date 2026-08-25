"""The agent's public entry point.

Constructs the collaborators once and holds the compiled graph, so a caller
that asks several questions pays the setup cost once and reuses the cached
schema. This is the only class the CLI, the demo and the evaluation harness
should use.
"""

from typing import Any

from src.graph.builder import build_insights_graph
from src.graph.nodes import InsightsGraphNodes
from src.interfaces import BigQueryReader
from src.llm.base import LLMClient
from src.result_analyzer import ResultAnalyzer
from src.schema_config import RELATIONSHIPS
from src.schema_provider import SchemaProvider
from src.sql_execution_pipeline import SQLExecutionPipeline
from src.sql_generator import SQLGenerator
from src.sql_repairer import SQLRepairer


class InsightsAgent:
    """Answer business questions over the configured BigQuery dataset."""

    def __init__(
        self,
        *,
        bigquery_service: BigQueryReader | None = None,
        llm: LLMClient | None = None,
        max_repair_attempts: int | None = None,
        relationships: list[str] | None = None,
    ) -> None:
        # Imported lazily so that constructing an agent with injected
        # collaborators never reaches for credentials or a model.
        if bigquery_service is None:
            from src.bigquery_service import BigQueryService

            bigquery_service = BigQueryService()

        if llm is None:
            from src.llm.ollama_client import OllamaGemmaClient

            llm = OllamaGemmaClient()

        self.bigquery_service = bigquery_service
        self.llm = llm

        self.schema_provider = SchemaProvider(
            bigquery_service=bigquery_service,
            relationships=(
                RELATIONSHIPS if relationships is None else relationships
            ),
        )

        self.nodes = InsightsGraphNodes(
            schema_provider=self.schema_provider,
            sql_generator=SQLGenerator(llm=llm),
            sql_execution_pipeline=SQLExecutionPipeline(
                bigquery_service=bigquery_service,
            ),
            sql_repairer=SQLRepairer(llm=llm),
            result_analyzer=ResultAnalyzer(llm=llm),
            max_repair_attempts=max_repair_attempts,
        )

        self.graph = build_insights_graph(nodes=self.nodes)

    def run(self, question: str) -> dict[str, Any]:
        """Answer one question.

        Returns the run record — the same structure written to the run log —
        with the result rows attached. Rows are excluded from the durable
        record on purpose, but a caller obviously needs them.
        """

        final_state = self.graph.invoke({"question": question})

        record: dict[str, Any] = dict(final_state.get("run_record") or {})

        execution_result = final_state.get("execution_result") or {}

        record["rows"] = execution_result.get("rows", [])

        return record

    def refresh_schema(self) -> None:
        """Drop the cached schema document.

        The provider caches for a TTL, so a dataset changed mid-session would
        otherwise not be visible until it expires.
        """

        self.schema_provider.invalidate_cache()
