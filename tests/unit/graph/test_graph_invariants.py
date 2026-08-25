"""The invariants Phase 7 exists to guarantee.

Written after an audit mutation-tested the Phase 7 suite and found four
regressions that slipped through: the repair_sql terminal edge, the
delegation to repair_policy, outcome classification, and duplication on the
sql_execution_run_ids accumulator.
"""

import ast
import inspect
import json
from pathlib import Path

import pytest
from google.api_core.exceptions import BadRequest

from src.agent import InsightsAgent
from src.exceptions import LLMProviderError
from src.graph import builder as builder_module
from src.graph.nodes import InsightsGraphNodes
from src.llm.scripted import ScriptedLLMClient
from tests.fakes import data
from tests.fakes.bigquery import FakeBigQueryService
from tests.fakes.factories import (
    WELL_FORMED_ANALYSIS,
    initial_state,
    make_insights_graph,
)

GOOD_SQL = data.VALID_SQL
UNQUALIFIED_SQL = "SELECT SUM(net_revenue) AS total FROM fact_sales"


def graph_records(run_log_path):
    if not run_log_path.exists():
        return []
    return [
        record
        for record in (
            json.loads(line)
            for line in run_log_path.read_text().splitlines()
            if line.strip()
        )
        if record.get("event") == "graph_run"
    ]


def make_agent(**kwargs) -> InsightsAgent:
    kwargs.setdefault("bigquery_service", FakeBigQueryService())
    kwargs.setdefault("relationships", list(data.RELATIONSHIPS))
    return InsightsAgent(**kwargs)


class TestSingleExitInvariant:
    """Every terminal path must reach finalize_run, including the odd ones."""

    def test_builder_has_no_path_to_END_except_finalize_run(self):
        """Read the builder's AST rather than trusting a comment.

        A conditional-edge map pointing at END instead of finalize_run would
        silently skip classification, logging and the run record for that one
        path — the kind of hole that only shows up in production.
        """

        source = inspect.getsource(builder_module.build_insights_graph)
        tree = ast.parse(inspect.cleandoc(source))

        end_targets = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == "END"
        ]

        # Exactly one: add_edge("finalize_run", END).
        assert len(end_targets) == 1, (
            "build_insights_graph should reach END only from finalize_run"
        )

    def test_repair_failure_still_reaches_finalize_run(self, run_log_path):
        """The repair_sql -> finalize_run edge."""

        service = FakeBigQueryService(
            dry_run_error=BadRequest("Unrecognized name: revenue")
        )
        graph = make_insights_graph(
            bigquery_service=service,
            llm=ScriptedLLMClient(
                [GOOD_SQL, LLMProviderError("model died mid-repair")]
            ),
            max_repair_attempts=2,
        )

        final = graph.invoke(initial_state())

        assert final["status"] == "error"
        assert final["terminal_stage"] == "sql_repair"
        assert "model died mid-repair" in final["error_message"]
        assert final["run_record"]
        assert len(graph_records(run_log_path)) == 1

    def test_analysis_failure_still_reaches_finalize_run(self, run_log_path):
        graph = make_insights_graph(
            llm=ScriptedLLMClient(
                [GOOD_SQL, LLMProviderError("model died mid-analysis")]
            )
        )

        final = graph.invoke(initial_state())

        assert final["status"] == "error"
        assert final["terminal_stage"] == "result_analysis"
        assert len(graph_records(run_log_path)) == 1

    @pytest.mark.parametrize(
        "node_name",
        [
            "get_schema",
            "generate_sql",
            "execute_sql",
            "repair_sql",
            "analyze_result",
        ],
    )
    def test_work_nodes_are_instrumented(self, node_name):
        node = getattr(InsightsGraphNodes, f"{node_name}_node")

        assert hasattr(node, "__wrapped__"), (
            f"{node_name}_node is not wrapped by timed_node"
        )

    @pytest.mark.parametrize(
        "node_name", ["initialize_run", "finalize_run"]
    )
    def test_lifecycle_nodes_are_deliberately_not_instrumented(
        self, node_name
    ):
        """finalize_run cannot appear in the trace it assembles."""

        node = getattr(InsightsGraphNodes, f"{node_name}_node")

        assert not hasattr(node, "__wrapped__")


class TestUnhandledExceptionsStillLeaveARecord:
    """Nodes handle expected failures; the agent guarantees the rest."""

    def test_agent_records_an_unexpected_exception(self, run_log_path):
        service = FakeBigQueryService()

        def explode(*args, **kwargs):
            raise RuntimeError("something nobody anticipated")

        service.get_dataset_structure = explode  # type: ignore[method-assign]

        agent = make_agent(
            bigquery_service=service, llm=ScriptedLLMClient([])
        )

        record = agent.run("What were net sales by month?")

        assert record["outcome"]["status"] == "error"
        assert record["outcome"]["terminal_stage"] == "unhandled_exception"
        assert "something nobody anticipated" in (
            record["outcome"]["error_message"]
        )
        assert len(graph_records(run_log_path)) == 1

    def test_agent_does_not_raise(self):
        service = FakeBigQueryService()

        def explode(*args, **kwargs):
            raise RuntimeError("boom")

        service.get_dataset_structure = explode  # type: ignore[method-assign]

        agent = make_agent(
            bigquery_service=service, llm=ScriptedLLMClient([])
        )

        # The point of the guarantee: a caller gets a result, not a traceback.
        record = agent.run("anything")

        assert record["rows"] == []

    def test_crash_record_names_the_failing_node(self):
        service = FakeBigQueryService()

        def explode(*args, **kwargs):
            raise RuntimeError("boom")

        service.get_dataset_structure = explode  # type: ignore[method-assign]

        agent = make_agent(
            bigquery_service=service, llm=ScriptedLLMClient([])
        )

        record = agent.run("anything")

        assert record["outcome"].get("failed_node") == "get_schema"

    def test_cli_returns_an_exit_code_rather_than_propagating(self, capsys):
        """The CLI reports a crash as a failed run, it does not die of it.

        A traceback does reach stderr, on purpose: logger.exception keeps it
        for whoever has to debug this. What must not happen is the exception
        escaping main() and killing the process before it can report.
        """

        from src.cli import EXIT_FAILED, main

        service = FakeBigQueryService()

        def explode(*args, **kwargs):
            raise RuntimeError("boom")

        service.get_dataset_structure = explode  # type: ignore[method-assign]

        agent = make_agent(
            bigquery_service=service, llm=ScriptedLLMClient([])
        )

        code = main(["a question"], agent=agent)
        captured = capsys.readouterr()

        assert code == EXIT_FAILED
        assert "unhandled_exception" in captured.err


class TestOutcomeClassification:
    """A wrong status here is an exit-code-0 lie about a failed run."""

    def test_success_requires_a_successful_execution(self):
        """error_stage being empty is not by itself evidence of success."""

        state = {
            "error_stage": "",
            "execution_result": {
                "status": "error",
                "stage": "execution",
            },
        }

        status, stage = InsightsGraphNodes._classify_outcome(state)

        assert status == "error"
        assert stage == "execution"

    def test_cleared_error_stage_on_a_rejection_is_still_a_rejection(self):
        state = {
            "error_stage": "",
            "execution_result": {
                "status": "rejected",
                "stage": "table_access",
            },
        }

        status, stage = InsightsGraphNodes._classify_outcome(state)

        assert status == "rejected"
        assert stage == "table_access"

    def test_nothing_happened_is_not_success(self):
        status, stage = InsightsGraphNodes._classify_outcome({})

        assert status == "error"
        assert stage == "incomplete"

    def test_execution_without_analysis_is_success_at_execution(self):
        state = {
            "error_stage": "",
            "execution_result": {"status": "success", "stage": "execution"},
        }

        assert InsightsGraphNodes._classify_outcome(state) == (
            "success",
            "execution",
        )

    def test_analysis_present_is_complete(self):
        state = {
            "error_stage": "",
            "execution_result": {"status": "success", "stage": "execution"},
            "business_analysis": "DIRECT ANSWER: ...",
        }

        assert InsightsGraphNodes._classify_outcome(state) == (
            "success",
            "complete",
        )

    def test_non_dict_execution_result_does_not_crash(self):
        """Every other consumer guards this, so this one must too."""

        state = {
            "error_stage": "",
            "execution_result": "not a dict",  # type: ignore[typeddict-item]
        }

        status, stage = InsightsGraphNodes._classify_outcome(state)

        assert status == "error"
        assert stage == "incomplete"


class TestRepairPolicyIsActuallyDelegated:
    """Phase 7 claims the policy has one definition. Prove it."""

    def test_route_after_execution_calls_should_repair(self, monkeypatch):
        from src.graph import nodes as nodes_module

        calls = []

        def spy(*, failure_stage, repairs_used, max_repair_attempts=None):
            calls.append(
                {
                    "failure_stage": failure_stage,
                    "repairs_used": repairs_used,
                    "max_repair_attempts": max_repair_attempts,
                }
            )
            return False

        monkeypatch.setattr(nodes_module, "should_repair", spy)

        graph = make_insights_graph(
            llm=ScriptedLLMClient([UNQUALIFIED_SQL]),
            max_repair_attempts=2,
        )

        graph.invoke(initial_state())

        assert calls, (
            "route_after_execution did not consult repair_policy; the "
            "routing rules have drifted back into a second copy"
        )
        assert calls[0]["failure_stage"] == "table_access"
        assert calls[0]["max_repair_attempts"] == 2

    def test_nodes_module_defines_no_second_stage_list(self):
        """Guard against the policy being re-inlined next to the router."""

        source = Path("src/graph/nodes.py").read_text()

        assert "REPAIRABLE_STAGES" not in source, (
            "the repairable-stage set belongs only in src/repair_policy.py"
        )


class TestAccumulatorsNeverDuplicate:
    """One test per accumulator; a whole-list return must be caught."""

    def test_sql_execution_run_ids_has_one_entry_per_execution(self):
        service = FakeBigQueryService(
            dry_run_error=BadRequest("Unrecognized name: revenue")
        )
        graph = make_insights_graph(
            bigquery_service=service,
            llm=ScriptedLLMClient([GOOD_SQL, GOOD_SQL, GOOD_SQL]),
            max_repair_attempts=2,
        )

        final = graph.invoke(initial_state())

        run_ids = final["sql_execution_run_ids"]

        # Three executions: the original plus two repairs.
        assert len(run_ids) == 3
        assert len(set(run_ids)) == 3, "run ids were duplicated"

    def test_llm_calls_has_one_entry_per_call(self):
        graph = make_insights_graph(
            llm=ScriptedLLMClient(
                [UNQUALIFIED_SQL, GOOD_SQL, WELL_FORMED_ANALYSIS]
            ),
            max_repair_attempts=1,
        )

        final = graph.invoke(initial_state())

        assert len(final["llm_calls"]) == 3

    def test_repair_history_has_one_entry_per_repair(self):
        graph = make_insights_graph(
            llm=ScriptedLLMClient(
                [UNQUALIFIED_SQL, GOOD_SQL, WELL_FORMED_ANALYSIS]
            ),
            max_repair_attempts=1,
        )

        final = graph.invoke(initial_state())

        assert len(final["repair_history"]) == 1

    def test_node_trace_has_one_entry_per_node_visit(self):
        graph = make_insights_graph(
            llm=ScriptedLLMClient([GOOD_SQL, WELL_FORMED_ANALYSIS])
        )

        final = graph.invoke(initial_state())

        assert len(final["node_trace"]) == 4

    def test_an_agent_reused_across_runs_does_not_leak_state(self):
        agent = make_agent(
            llm=ScriptedLLMClient(
                [
                    GOOD_SQL,
                    WELL_FORMED_ANALYSIS,
                    GOOD_SQL,
                    WELL_FORMED_ANALYSIS,
                ]
            )
        )

        first = agent.run("What were net sales by month?")
        second = agent.run("What were net sales by month?")

        assert first["graph_run_id"] != second["graph_run_id"]
        for record in (first, second):
            assert len(record["runtime"]["node_trace"]) == 4
            assert record["tokens"]["totals"]["llm_call_count"] == 2


class TestSerialization:
    """BigQuery hands back Decimal and date; the record must survive them."""

    def test_decimal_and_date_values_serialize(self, run_log_path):
        from datetime import date
        from decimal import Decimal

        service = FakeBigQueryService(
            rows=[
                {
                    "month": date(2025, 1, 1),
                    "total": Decimal("1462.66"),
                }
            ]
        )
        agent = make_agent(
            bigquery_service=service,
            llm=ScriptedLLMClient([GOOD_SQL, WELL_FORMED_ANALYSIS]),
        )

        record = agent.run("What were net sales by month?")

        assert record["outcome"]["status"] == "success"
        # Written to disk without raising is the round-trip proof.
        assert len(graph_records(run_log_path)) == 1
