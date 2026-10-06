"""The harness and report, driven entirely by fakes.

An evaluation that only runs live is one nobody runs. These prove the
scoring, the pass/fail logic and the report render correctly without
BigQuery or a model.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from evaluation.cases import (
    PROJECT_PLACEHOLDER,
    GoldenCase,
    load_attack_cases,
)
from evaluation.harness import (
    classify_failure,
    evaluate_case,
    execution_succeeded,
)
from evaluation.report import build_json_report, render_markdown
from evaluation.run_evaluation import run_guardrail_suite
from src.agent import InsightsAgent
from src.llm.scripted import ScriptedLLMClient
from src.sql_execution_pipeline import SQLExecutionPipeline
from tests.fakes import data
from tests.fakes.bigquery import FakeBigQueryService
from tests.fakes.factories import WELL_FORMED_ANALYSIS

AGENT_SQL = data.VALID_SQL
REFERENCE_SQL = (
    "SELECT FORMAT_DATE('%Y-%m', sale_date) AS m, "
    "SUM(net_revenue) AS v "
    f"FROM `{data.DATASET_PATH}.fact_sales` GROUP BY m"
)


def make_case(**overrides) -> GoldenCase:
    defaults = dict(
        case_id="demo_case",
        question="What were net sales by month?",
        category="aggregation",
        reference_sql=REFERENCE_SQL,
        expected_tables=[f"{data.DATASET_PATH}.fact_sales"],
        comparison="multiset",
        numeric_tolerance=0.01,
        max_repair_attempts=2,
    )
    defaults.update(overrides)
    return GoldenCase(**defaults)


def make_agent(service, llm) -> InsightsAgent:
    return InsightsAgent(
        bigquery_service=service,
        llm=llm,
        relationships=list(data.RELATIONSHIPS),
    )


class TestScoringAPassingCase:
    def test_a_correct_answer_passes_every_metric(self):
        service = FakeBigQueryService()
        agent = make_agent(
            service, ScriptedLLMClient([AGENT_SQL, WELL_FORMED_ANALYSIS])
        )

        result = evaluate_case(
            make_case(),
            agent=agent,
            execution_pipeline=SQLExecutionPipeline(
                bigquery_service=service
            ),
        )

        assert result.passed
        assert {score.name for score in result.scores.scores} == {
            "guardrail_pass",
            "executed",
            "table_grounding",
            "execution_accuracy",
            "repair_efficiency",
            "analysis_grounding",
        }

    def test_the_reference_query_runs_through_the_same_pipeline(self):
        """Both sides must be subject to the same caps and normalisation."""

        service = FakeBigQueryService()
        agent = make_agent(
            service, ScriptedLLMClient([AGENT_SQL, WELL_FORMED_ANALYSIS])
        )

        evaluate_case(
            make_case(),
            agent=agent,
            execution_pipeline=SQLExecutionPipeline(
                bigquery_service=service
            ),
        )

        # One execution for the agent, one for the reference.
        assert len(service.run_query_calls) == 2


class TestScoringFailures:
    def test_a_wrong_result_set_fails_execution_accuracy(self):
        """The agent and the reference return different numbers."""

        service = FakeBigQueryService()

        # The reference query gets different rows than the agent's query.
        # Keyed on the reference's distinctive alias — note "GROUP BY m"
        # would also match the agent's "GROUP BY month".
        def rows_for(sql):
            if "AS v" in sql:
                return [{"m": "2025-01", "v": Decimal("999.99")}]
            return list(data.MONTHLY_SALES_ROWS)

        original = service.run_query

        def patched(sql, **kwargs):
            service.rows = rows_for(sql)
            return original(sql, **kwargs)

        service.run_query = patched  # type: ignore[method-assign]

        agent = make_agent(
            service, ScriptedLLMClient([AGENT_SQL, WELL_FORMED_ANALYSIS])
        )

        result = evaluate_case(
            make_case(),
            agent=agent,
            execution_pipeline=SQLExecutionPipeline(
                bigquery_service=service
            ),
        )

        assert not result.passed
        assert not result.scores.by_name()["execution_accuracy"].passed

    def test_a_guardrail_refusal_fails_and_skips_later_metrics(self):
        service = FakeBigQueryService()
        agent = make_agent(
            service,
            ScriptedLLMClient(
                ["SELECT SUM(net_revenue) FROM fact_sales"] * 4
            ),
        )

        result = evaluate_case(
            make_case(),
            agent=agent,
            execution_pipeline=SQLExecutionPipeline(
                bigquery_service=service
            ),
        )

        by_name = result.scores.by_name()

        assert not result.passed
        assert not by_name["guardrail_pass"].passed
        # Downstream metrics are not scored on a run that never executed.
        assert "execution_accuracy" not in by_name

    def test_an_invented_figure_fails_analysis_grounding(self):
        service = FakeBigQueryService()
        analysis = WELL_FORMED_ANALYSIS.replace(
            "KEY INSIGHTS:",
            "KEY INSIGHTS:\n- A total of $77,777.77.",
        )

        agent = make_agent(
            service, ScriptedLLMClient([AGENT_SQL, analysis])
        )

        result = evaluate_case(
            make_case(),
            agent=agent,
            execution_pipeline=SQLExecutionPipeline(
                bigquery_service=service
            ),
        )

        assert not result.passed
        assert not result.scores.by_name()["analysis_grounding"].passed

    def test_the_wrong_table_fails_table_grounding(self):
        service = FakeBigQueryService()
        agent = make_agent(
            service, ScriptedLLMClient([AGENT_SQL, WELL_FORMED_ANALYSIS])
        )

        result = evaluate_case(
            make_case(
                expected_tables=[f"{data.DATASET_PATH}.dim_products"]
            ),
            agent=agent,
            execution_pipeline=SQLExecutionPipeline(
                bigquery_service=service
            ),
        )

        assert not result.scores.by_name()["table_grounding"].passed

    def test_a_broken_reference_query_fails_loudly(self):
        """A dataset fault must not silently score as a pass."""

        service = FakeBigQueryService()
        agent = make_agent(
            service, ScriptedLLMClient([AGENT_SQL, WELL_FORMED_ANALYSIS])
        )

        result = evaluate_case(
            make_case(reference_sql="DELETE FROM `p.d.t` WHERE TRUE"),
            agent=agent,
            execution_pipeline=SQLExecutionPipeline(
                bigquery_service=service
            ),
        )

        score = result.scores.by_name()["execution_accuracy"]

        assert not score.passed
        assert "reference query failed" in score.detail


class TestGuardrailSuite:
    def test_every_adversarial_case_is_refused(self):
        pipeline = SQLExecutionPipeline(
            bigquery_service=FakeBigQueryService(
                project_id="your-project-id",
                dataset_id="business_insights",
            )
        )

        summary = run_guardrail_suite(
            pipeline, load_attack_cases(project_id=PROJECT_PLACEHOLDER)
        )

        assert summary["blocked"] == summary["total"]
        assert summary["failures"] == []
        assert summary["total"] >= 15

    def test_ambient_project_id_cannot_change_the_hermetic_result(
        self, monkeypatch
    ):
        from src import config

        monkeypatch.setattr(config, "PROJECT_ID", "acme-real-123")
        pipeline = SQLExecutionPipeline(
            bigquery_service=FakeBigQueryService(
                project_id=PROJECT_PLACEHOLDER,
                dataset_id="business_insights",
            )
        )

        summary = run_guardrail_suite(
            pipeline, load_attack_cases(project_id=PROJECT_PLACEHOLDER)
        )

        assert summary["blocked"] == summary["total"]
        assert summary["failures"] == []

    def test_live_substitution_is_blocked_too(self):
        pipeline = SQLExecutionPipeline(
            bigquery_service=FakeBigQueryService(
                project_id="acme-real-123",
                dataset_id="business_insights",
            )
        )

        summary = run_guardrail_suite(
            pipeline, load_attack_cases(project_id="acme-real-123")
        )

        assert summary["blocked"] == summary["total"]
        assert summary["failures"] == []


class TestReporting:
    @pytest.fixture
    def results(self):
        service = FakeBigQueryService()
        pipeline = SQLExecutionPipeline(bigquery_service=service)

        passing = evaluate_case(
            make_case(case_id="passing_case"),
            agent=make_agent(
                service,
                ScriptedLLMClient([AGENT_SQL, WELL_FORMED_ANALYSIS]),
            ),
            execution_pipeline=pipeline,
        )

        failing = evaluate_case(
            make_case(
                case_id="failing_case",
                expected_tables=["p.d.nonexistent"],
            ),
            agent=make_agent(
                service,
                ScriptedLLMClient([AGENT_SQL, WELL_FORMED_ANALYSIS]),
            ),
            execution_pipeline=pipeline,
        )

        return [passing, failing]

    def test_markdown_reports_the_pass_rate(self, results):
        markdown = render_markdown(results, model_name="test-model")

        assert "# Evaluation report" in markdown
        assert "Passed: **1**" in markdown
        assert "test-model" in markdown

    def test_markdown_lists_every_case(self, results):
        markdown = render_markdown(results)

        assert "passing_case" in markdown
        assert "failing_case" in markdown

    def test_markdown_explains_each_failure(self, results):
        markdown = render_markdown(results)

        assert "## Failures" in markdown
        assert "table_grounding" in markdown
        # The agent's SQL is shown so the failure is actionable.
        assert "SELECT" in markdown

    def test_markdown_includes_the_guardrail_section(self, results):
        markdown = render_markdown(
            results,
            attack_summary={
                "total": 19,
                "blocked": 19,
                "by_stage": {"validation": 11, "table_access": 7},
            },
        )

        assert "19/19" in markdown
        assert "before a byte was" in markdown

    def test_json_report_round_trips(self, results):
        import json

        payload = build_json_report(results, model_name="test-model")

        restored = json.loads(json.dumps(payload, default=str))

        assert restored["summary"]["cases"] == 2
        assert restored["summary"]["passed"] == 1
        assert len(restored["cases"]) == 2

    def test_summary_reports_latency_and_tokens(self, results):
        payload = build_json_report(results)
        summary = payload["summary"]

        assert summary["latency_ms"]["mean"] >= 0
        assert summary["tokens"]["total"] > 0
        assert "p95" in summary["latency_ms"]


class TestExecutionSucceeded:
    def test_success_complete(self):
        assert execution_succeeded(
            {"outcome": {"status": "success", "terminal_stage": "complete"}}
        )

    def test_success_execution(self):
        assert execution_succeeded(
            {"outcome": {"status": "success", "terminal_stage": "execution"}}
        )

    def test_error_at_result_analysis_with_rows(self):
        assert execution_succeeded(
            {
                "outcome": {
                    "status": "error",
                    "terminal_stage": "result_analysis",
                    "error_stage": "result_analysis",
                },
                "data": {"row_count": 3},
            }
        )

    def test_result_analysis_without_rows_is_not_execution(self):
        assert not execution_succeeded(
            {
                "outcome": {
                    "status": "error",
                    "error_stage": "result_analysis",
                },
                "data": {"row_count": None},
            }
        )

    def test_rejected_at_validation(self):
        assert not execution_succeeded(
            {
                "outcome": {
                    "status": "rejected",
                    "terminal_stage": "validation",
                    "error_stage": "validation",
                },
                "data": {"row_count": None},
            }
        )

    def test_error_at_dry_run(self):
        assert not execution_succeeded(
            {"outcome": {"status": "error", "error_stage": "dry_run"}}
        )

    def test_crash_record(self):
        assert not execution_succeeded({})


class TestSupplementaryAccuracy:
    def test_scored_when_analysis_fails_after_good_sql(self):
        service = FakeBigQueryService()
        pipeline = SQLExecutionPipeline(bigquery_service=service)
        # Analysis response violates the contract and cannot be repaired.
        agent = make_agent(
            service, ScriptedLLMClient([AGENT_SQL, ""] * 3)
        )

        result = evaluate_case(
            make_case(), agent=agent, execution_pipeline=pipeline
        )

        status = result.record["outcome"]["status"]
        if status == "success":
            pytest.skip("analysis did not fail with this script")

        assert result.execution_succeeded
        assert [s.name for s in result.supplementary] == [
            "sql_accuracy_any_outcome"
        ]
        assert result.supplementary[0].passed
        # Never enters the legacy scores or the pass/fail decision.
        assert "sql_accuracy_any_outcome" not in result.scores.by_name()

    def test_passing_run_also_has_a_supplementary_score(self):
        service = FakeBigQueryService()
        pipeline = SQLExecutionPipeline(bigquery_service=service)
        agent = make_agent(
            service, ScriptedLLMClient([AGENT_SQL, WELL_FORMED_ANALYSIS])
        )

        result = evaluate_case(
            make_case(), agent=agent, execution_pipeline=pipeline
        )

        assert result.execution_succeeded
        assert result.supplementary[0].passed

    def test_rejected_run_has_none(self):
        service = FakeBigQueryService()
        pipeline = SQLExecutionPipeline(bigquery_service=service)
        agent = make_agent(
            service, ScriptedLLMClient(["DROP TABLE x"] * 5)
        )

        result = evaluate_case(
            make_case(), agent=agent, execution_pipeline=pipeline
        )

        assert not result.execution_succeeded
        assert result.supplementary == []
        payload = build_json_report([result])
        assert payload["summary"]["execution_coverage"] == {
            "executed": 0, "attempted": 1,
        }
        assert payload["cases"][0]["supplementary"] == {}


REAL_PROJECT = "acme-real-123"
GOLDEN_ID = "net_sales_by_month_2025"


def _manifest(all_match=True):
    return {
        "schema_version": 1,
        "git": {"head": "h", "branch": "b", "dirty_files": [],
                "diff_sha256": "x"},
        "dataset": {"reconciliation": [], "all_match": all_match, "csv": {}},
        "model": {"tag": "m", "digest": "d"},
        "config": {},
        "trials": {"count": 2, "notes": ""},
    }


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Patch run_evaluation.main's collaborators with fakes."""

    import evaluation.run_evaluation as run_eval
    import src.agent
    import src.bigquery_service
    from src import config

    monkeypatch.setattr(config, "PROJECT_ID", REAL_PROJECT)

    service = FakeBigQueryService(project_id=REAL_PROJECT)
    sql = data.VALID_SQL.replace(data.PROJECT_ID, REAL_PROJECT)

    def make_llm(runs=3):
        return ScriptedLLMClient([sql, WELL_FORMED_ANALYSIS] * runs)

    state = {"llm": make_llm()}

    monkeypatch.setattr(
        src.bigquery_service, "BigQueryService", lambda *a, **k: service
    )
    monkeypatch.setattr(
        src.agent,
        "InsightsAgent",
        lambda **kwargs: InsightsAgent(
            bigquery_service=service,
            llm=state["llm"],
            relationships=list(data.RELATIONSHIPS),
        ),
    )
    monkeypatch.setattr(
        run_eval,
        "ollama_model_identity",
        lambda *a, **k: {"tag": "m", "digest": "d", "loaded_models": []},
    )
    monkeypatch.setattr(
        run_eval, "build_manifest", lambda **kw: _manifest(True)
    )

    return types_ns(run_eval=run_eval, state=state, tmp=tmp_path,
                    monkeypatch=monkeypatch)


def types_ns(**kwargs):
    import types

    return types.SimpleNamespace(**kwargs)


def _argv(tmp, *extra):
    return [
        "--case", GOLDEN_ID, "--trials", "2", "--report-name", "x",
        "--warm-up-question", "hello",
        "--run-log", str(tmp / "runs.jsonl"),
        *extra,
    ]


class TestMainEndToEnd:
    def test_writes_only_named_redacted_reports(self, wired):
        import json

        reports = wired.tmp / "reports"
        code = wired.run_eval.main(_argv(wired.tmp), reports_dir=reports)

        assert code == 0
        assert sorted(p.name for p in reports.iterdir()) == [
            "x.json", "x.md",
        ]
        md = (reports / "x.md").read_text()
        raw = (reports / "x.json").read_text()
        assert REAL_PROJECT not in md
        assert REAL_PROJECT not in raw

        payload = json.loads(raw)
        assert payload["summary"]["cases"] == 2
        assert len(payload["summary"]["by_trial"]) == 2
        trials = payload["manifest"]["trials"]
        assert trials["warm_up"]["status"] == "success"
        assert trials["warm_up"]["llm_call_count"] == 2
        assert trials["run_log_file"].endswith("runs.jsonl")

        lines = [
            json.loads(line)
            for line in (wired.tmp / "runs.jsonl").read_text().splitlines()
        ]
        graph_runs = [r for r in lines if r.get("event") == "graph_run"]
        assert len(graph_runs) == 3
        assert graph_runs[0]["graph_run_id"] == (
            trials["warm_up"]["graph_run_id"]
        )

    def test_dataset_mismatch_aborts_without_writing(self, wired):
        wired.monkeypatch.setattr(
            wired.run_eval, "build_manifest", lambda **kw: _manifest(False)
        )
        reports = wired.tmp / "reports"

        code = wired.run_eval.main(_argv(wired.tmp), reports_dir=reports)

        assert code == 2
        assert not reports.exists() or list(reports.iterdir()) == []

    def test_allow_manifest_errors_marks_the_report(self, wired):
        wired.monkeypatch.setattr(
            wired.run_eval, "build_manifest", lambda **kw: _manifest(False)
        )
        reports = wired.tmp / "reports"

        code = wired.run_eval.main(
            _argv(wired.tmp, "--allow-manifest-errors"), reports_dir=reports
        )

        assert code == 0
        assert "WARNING" in (reports / "x.md").read_text()

    def test_manifest_exception_aborts(self, wired):
        def boom(**kw):
            raise RuntimeError("nope")

        wired.monkeypatch.setattr(wired.run_eval, "build_manifest", boom)
        reports = wired.tmp / "reports"

        assert wired.run_eval.main(
            _argv(wired.tmp), reports_dir=reports
        ) == 2
        assert not reports.exists() or list(reports.iterdir()) == []

    def test_unavailable_model_in_warm_up_aborts(self, wired):
        from src.exceptions import LLMProviderError

        wired.state["llm"] = ScriptedLLMClient(
            [LLMProviderError("Ollama is not reachable")] * 3
        )
        reports = wired.tmp / "reports"

        code = wired.run_eval.main(_argv(wired.tmp), reports_dir=reports)

        assert code == 2
        assert not reports.exists() or list(reports.iterdir()) == []

    def test_unavailable_model_aborts_even_with_allow_flag(self, wired):
        from src.exceptions import LLMProviderError

        wired.state["llm"] = ScriptedLLMClient(
            [LLMProviderError("Ollama is not reachable")] * 3
        )
        reports = wired.tmp / "reports"

        code = wired.run_eval.main(
            _argv(wired.tmp, "--allow-manifest-errors"), reports_dir=reports
        )

        assert code == 2
        assert not reports.exists() or list(reports.iterdir()) == []

    def test_a_section_error_aborts(self, wired):
        manifest = _manifest(True)
        manifest["git"] = {"error": "CalledProcessError: nope"}
        wired.monkeypatch.setattr(
            wired.run_eval, "build_manifest", lambda **kw: manifest
        )
        reports = wired.tmp / "reports"

        assert wired.run_eval.main(
            _argv(wired.tmp), reports_dir=reports
        ) == 2
        assert not reports.exists() or list(reports.iterdir()) == []

    def test_section_error_allowed_is_recorded_in_the_json(self, wired):
        import json

        manifest = _manifest(True)
        manifest["environment"] = {"error": "boom"}
        wired.monkeypatch.setattr(
            wired.run_eval, "build_manifest", lambda **kw: manifest
        )
        reports = wired.tmp / "reports"

        code = wired.run_eval.main(
            _argv(wired.tmp, "--allow-manifest-errors"), reports_dir=reports
        )

        assert code == 0
        summary = json.loads((reports / "x.json").read_text())["summary"]
        assert summary["invalid_baseline"] is True
        assert any(
            "environment" in r for r in summary["invalid_baseline_reasons"]
        )
        assert "WARNING" in (reports / "x.md").read_text()

    def test_clean_run_is_a_valid_baseline(self, wired):
        import json

        reports = wired.tmp / "reports"
        assert wired.run_eval.main(
            _argv(wired.tmp), reports_dir=reports
        ) == 0
        summary = json.loads((reports / "x.json").read_text())["summary"]
        assert summary["invalid_baseline"] is False
        assert summary["invalid_baseline_reasons"] == []

    def test_default_path_writes_latest_and_timestamped(self, wired):
        reports = wired.tmp / "reports"
        argv = [
            "--case", GOLDEN_ID,
            "--run-log", str(wired.tmp / "runs.jsonl"),
        ]

        code = wired.run_eval.main(argv, reports_dir=reports)

        assert code == 0
        names = sorted(p.name for p in reports.iterdir())
        assert len(names) == 4
        assert {"latest.json", "latest.md"} <= set(names)
        stamped = [n for n in names if n.startswith("report-")]
        assert sorted(Path(n).suffix for n in stamped) == [".json", ".md"]

    def test_failed_manifest_keeps_the_model_identity(self, wired):
        import json

        def boom(**kw):
            raise RuntimeError("nope")

        wired.monkeypatch.setattr(wired.run_eval, "build_manifest", boom)
        reports = wired.tmp / "reports"

        code = wired.run_eval.main(
            _argv(wired.tmp, "--allow-manifest-errors"), reports_dir=reports
        )

        assert code == 0
        manifest = json.loads((reports / "x.json").read_text())["manifest"]
        assert manifest["model"] == {
            "tag": "m", "digest": "d", "loaded_models": [],
        }
        assert "nope" in manifest["error"]

    def test_existing_report_needs_overwrite(self, wired):
        reports = wired.tmp / "reports"
        reports.mkdir()
        (reports / "x.md").write_text("old")

        code = wired.run_eval.main(_argv(wired.tmp), reports_dir=reports)

        assert code == 2
        assert (reports / "x.md").read_text() == "old"
        assert not (wired.tmp / "runs.jsonl").exists()

        code = wired.run_eval.main(
            _argv(wired.tmp, "--overwrite"), reports_dir=reports
        )
        assert code == 0

    @pytest.mark.parametrize("name", ["latest", "../x", "report-1"])
    def test_bad_report_names_exit(self, name):
        from evaluation.run_evaluation import main

        with pytest.raises(SystemExit):
            main(["--report-name", name])


class TestClassifyFailure:
    @staticmethod
    def _record(status, message=None):
        return {"outcome": {"status": status, "error_message": message}}

    @pytest.mark.parametrize(
        "message",
        [
            "Read timed out after 120s",
            "Ollama is not reachable",
            "Unable to communicate with the model",
            "connection refused",
            "503 Service Unavailable",
            "LLMProviderError: boom",
        ],
    )
    def test_provider_trouble_is_unavailable(self, message):
        assert (
            classify_failure(self._record("error", message))
            == "unavailable"
        )

    def test_plain_error_stays_error(self):
        assert (
            classify_failure(self._record("error", "KeyError: 'rows'"))
            == "error"
        )

    def test_success_and_rejected_pass_through(self):
        assert classify_failure(self._record("success")) == "success"
        assert (
            classify_failure(self._record("rejected", "timeout in text"))
            == "rejected"
        )

    def test_case_result_and_summary_carry_the_class(self):
        service = FakeBigQueryService()
        pipeline = SQLExecutionPipeline(bigquery_service=service)
        agent = make_agent(
            service, ScriptedLLMClient([AGENT_SQL, WELL_FORMED_ANALYSIS])
        )

        result = evaluate_case(
            make_case(), agent=agent, execution_pipeline=pipeline
        )

        assert result.failure_class == "success"
        payload = build_json_report([result])
        assert payload["cases"][0]["failure_class"] == "success"
        assert payload["summary"]["failures"] == {
            "rejected": 0, "error": 0, "unavailable": 0,
        }
