"""The provenance manifest, driven entirely by fakes and tmp_path."""

import json
import subprocess
from decimal import Decimal

import pytest

from evaluation import manifest as mf
from evaluation.cases import GoldenCase
from evaluation.harness import CaseResult
from evaluation.report import build_json_report, render_markdown
from evaluation.run_evaluation import build_parser
from evaluation.scorers import CaseScores
from src.sql_execution_pipeline import SQLExecutionPipeline
from tests.fakes import data
from tests.fakes.bigquery import FakeBigQueryService


@pytest.fixture
def csv_dir(tmp_path):
    d = tmp_path / "csv"
    d.mkdir()
    (d / "fact_sales.csv").write_text(
        "sale_id,sale_date,quantity,net_revenue,profit_amount\n"
        "S1,2025-04-10,2,10.10,1.05\n"
        "S2,2025-01-02,3,20.20,2.10\n"
        "S3,2025-06-30,5,0.30,0.10\n"
    )
    (d / "dim_products.csv").write_text(
        "product_id,unit_cost,list_price\n"
        "P1,1.10,2.25\nP2,2.20,3.50\n"
    )
    (d / "dim_customers.csv").write_text(
        "customer_id,membership_status\n"
        "C1,Member\nC2,Non-Member\nC3,Member\nC4,Non-Member\n"
    )
    return d


EXPECTED_CSV = {
    "fact_sales": {
        "row_count": 3,
        "distinct_sale_id": 3,
        "sum_net_revenue": "30.60",
        "sum_profit_amount": "3.25",
        "sum_quantity": 10,
        "min_sale_date": "2025-01-02",
        "max_sale_date": "2025-06-30",
    },
    "dim_products": {
        "row_count": 2,
        "sum_list_price": "5.75",
        "sum_unit_cost": "3.30",
    },
    "dim_customers": {"row_count": 4, "member_count": 2},
}


def _stub_provider(model, host, timeout):
    return {
        "tag": model,
        "digest": "abc123",
        "family": "gemma",
        "parameter_size": "8B",
        "quantization_level": "Q4_K_M",
        "modified_at": "2026-01-01",
        "server_version": "0.9.0",
        "loaded_models": [{"model": model}],
    }


class TestCsv:
    def test_fingerprints(self, csv_dir):
        prints = mf.csv_fingerprints(csv_dir)

        assert set(prints) == {
            "fact_sales.csv",
            "dim_products.csv",
            "dim_customers.csv",
        }
        assert prints["fact_sales.csv"]["rows"] == 3
        assert prints["dim_customers.csv"]["rows"] == 4
        assert len(prints["fact_sales.csv"]["sha256"]) == 64

    def test_aggregates_use_exact_decimals(self, csv_dir):
        assert mf.csv_aggregates(csv_dir) == EXPECTED_CSV


class TestLiveAggregates:
    def _live_row(self):
        return {
            "row_count": 3,
            "distinct_sale_id": 3,
            "sum_net_revenue": Decimal("30.6"),
            "sum_profit_amount": 3.25,
            "sum_quantity": 10,
            "min_sale_date": "2025-01-02",
            "max_sale_date": "2025-06-30",
            "sum_list_price": Decimal("5.75"),
            "sum_unit_cost": Decimal("3.3"),
            "member_count": 2,
        }

    def test_three_reads_all_through_the_pipeline(self):
        service = FakeBigQueryService(rows=[self._live_row()])
        pipeline = SQLExecutionPipeline(bigquery_service=service)

        live = mf.live_aggregates(pipeline, data.DATASET_PATH)

        assert len(service.run_query_calls) == 3
        # They arrived via the pipeline, which dry-runs before it runs.
        assert len(service.dry_run_calls) == 3
        assert live["fact_sales"]["sum_net_revenue"] == "30.60"
        assert live["fact_sales"]["sum_profit_amount"] == "3.25"
        assert live["dim_customers"]["member_count"] == 2
        assert live["fact_sales"]["evidence"]["run_id"]
        assert live["fact_sales"]["evidence"]["job_id"] == "fake-job-1"
        assert all(
            f"`{data.DATASET_PATH}.{table}`" in sql
            for table, sql in zip(
                ["fact_sales", "dim_products", "dim_customers"],
                service.run_query_calls,
            )
        )

    def test_reconciles_with_csv(self):
        service = FakeBigQueryService(rows=[self._live_row()])
        pipeline = SQLExecutionPipeline(bigquery_service=service)
        live = mf.live_aggregates(pipeline, data.DATASET_PATH)

        rows = mf.reconcile(EXPECTED_CSV, live)

        assert len(rows) == 12
        # One canned row serves all three tables: fact_sales and the money
        # figures agree, the differing row counts must not.
        assert all(r["match"] for r in rows if r["table"] == "fact_sales")
        mismatched = {(r["table"], r["figure"]) for r in rows if not r["match"]}
        assert mismatched == {
            ("dim_products", "row_count"),
            ("dim_customers", "row_count"),
        }

    def test_failure_is_recorded_not_raised(self):
        service = FakeBigQueryService(run_query_error=RuntimeError("boom"))
        pipeline = SQLExecutionPipeline(bigquery_service=service)

        live = mf.live_aggregates(pipeline, data.DATASET_PATH)

        assert set(live) == {"fact_sales", "dim_products", "dim_customers"}
        assert all("boom" in entry["error"] for entry in live.values())
        assert all(
            not row["match"] for row in mf.reconcile(EXPECTED_CSV, live)
        )


class TestReconcile:
    def test_match_mismatch_and_missing(self):
        csv = {"t": {"a": 1, "b": "2.00", "c": 3}}
        live = {"t": {"a": 1, "b": "2.01"}}

        by_figure = {r["figure"]: r for r in mf.reconcile(csv, live)}

        assert by_figure["a"]["match"] is True
        assert by_figure["b"]["match"] is False
        assert by_figure["c"]["match"] is False

    def test_missing_table_never_raises(self):
        rows = mf.reconcile({"t": {"a": 1}}, {})
        assert rows == [
            {"table": "t", "figure": "a", "csv": 1, "bigquery": None,
             "match": False}
        ]


class TestModelIdentity:
    def test_stub_provider(self):
        identity = mf.ollama_model_identity(
            "gemma4", "http://h", 1.0, provider=_stub_provider
        )
        assert identity["digest"] == "abc123"
        assert identity["loaded_models"] == [{"model": "gemma4"}]
        assert "error" not in identity

    def test_raising_provider_reports_error(self):
        def boom(*_):
            raise ConnectionError("down")

        identity = mf.ollama_model_identity(
            "gemma4", "http://h", 1.0, provider=boom
        )
        assert "down" in identity["error"]
        assert identity["tag"] == "gemma4"


class TestGit:
    def test_identity_of_a_temp_repo(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()

        def git(*args):
            subprocess.run(
                ["git", "-c", "user.email=a@b.c", "-c", "user.name=t", *args],
                cwd=repo,
                check=True,
                capture_output=True,
            )

        git("init", "-q")
        (repo / "a.txt").write_text("one\n")
        git("add", ".")
        git("commit", "-q", "-m", "init")

        clean = mf.git_identity(repo)
        assert len(clean["head"]) == 40
        assert clean["dirty_files"] == []
        assert clean["diff_sha256"] == (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

        (repo / "a.txt").write_text("two\n")
        dirty = mf.git_identity(repo)
        assert any("a.txt" in line for line in dirty["dirty_files"])
        assert dirty["diff_sha256"] != clean["diff_sha256"]

    def test_failure_returns_error(self, tmp_path):
        assert "error" in mf.git_identity(tmp_path / "missing")


class TestBuildManifest:
    def test_end_to_end(self, csv_dir, tmp_path):
        row = TestLiveAggregates()._live_row()
        service = FakeBigQueryService(rows=[row])
        pipeline = SQLExecutionPipeline(bigquery_service=service)
        cases_path = tmp_path / "golden_cases.yaml"
        cases_path.write_text(
            "- case_id: c1\n  question: q\n  category: x\n"
            "  expect:\n    comparison: none\n"
        )

        manifest = mf.build_manifest(
            repo_root=tmp_path,
            bigquery_service=service,
            execution_pipeline=pipeline,
            csv_dir=csv_dir,
            case_paths=[cases_path],
            model_identity=mf.ollama_model_identity(
                "gemma4", "h", 1.0, provider=_stub_provider
            ),
            trials=3,
            notes="warm",
        )

        for key in (
            "schema_version", "git", "dataset", "model", "config",
            "cases", "environment", "trials",
        ):
            assert key in manifest
        assert isinstance(manifest["dataset"]["all_match"], bool)
        assert manifest["dataset"]["all_match"] is False
        assert manifest["cases"]["golden_cases.yaml"]["case_ids"] == ["c1"]
        assert manifest["trials"] == {"count": 3, "notes": "warm"}
        assert {t["table_name"] for t in manifest["dataset"]["live_tables"]} \
            == set(data.TABLE_NAMES)
        json.dumps(manifest, default=str)


def _result(trial, passed_status="success"):
    record = {
        "graph_run_id": f"g{trial}",
        "outcome": {"status": passed_status, "terminal_stage": "analysis"},
        "sql": {"executed_sql": "SELECT 1", "referenced_tables": ["t"]},
        "data": {"row_count": 1},
        "cost": {"cache_hit": False},
        "runtime": {
            "node_trace": [
                {"node": "generate_sql", "duration_ms": 100.0, "ok": True},
                {"node": "execute", "duration_ms": 50.0, "ok": True},
            ]
        },
        "tokens": {
            "llm_calls": [
                {
                    "purpose": "sql_generation",
                    "response_time_ms": 900.0,
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                }
            ],
            "totals": {"total_tokens": 15},
        },
        "analysis": {"is_grounded": True, "ungrounded_numbers": []},
    }
    case = GoldenCase(
        case_id="c1", question="q?", category="agg", reference_sql=""
    )
    return CaseResult(
        case=case,
        record=record,
        scores=CaseScores(case_id="c1"),
        duration_ms=1000.0 * trial,
        trial=trial,
    )


class TestReport:
    def test_json_and_markdown_with_two_trials(self):
        results = [_result(1), _result(2)]
        manifest = {
            "git": {"head": "deadbeef", "branch": "main", "dirty_files": [],
                    "diff_sha256": "x"},
            "model": {"tag": "gemma4", "digest": "abc"},
            "config": {"OLLAMA_TEMPERATURE": 0.0},
            "dataset": {"reconciliation": [], "all_match": False,
                        "csv": {}},
        }

        payload = build_json_report(results, manifest=manifest)
        markdown = render_markdown(results, manifest=manifest)

        assert payload["manifest"] == manifest
        assert len(payload["summary"]["by_trial"]) == 2
        assert payload["summary"]["latency_ms"]["median"] >= 0
        assert payload["summary"]["outcomes"] == {"success": 2}
        assert payload["summary"]["llm_latency_ms"]["sql_generation"][
            "count"
        ] == 2
        first = payload["cases"][0]
        assert [c["trial"] for c in payload["cases"]] == [1, 2]
        assert first["node_timings_ms"] == {
            "generate_sql": {"total_ms": 100.0, "count": 1},
            "execute": {"total_ms": 50.0, "count": 1},
        }
        assert [n["node"] for n in first["node_trace"]] == [
            "generate_sql", "execute",
        ]
        assert first["llm_calls"][0]["total_tokens"] == 15
        assert first["outcome"]["status"] == "success"
        assert first["executed_sql"] == "SELECT 1"
        json.dumps(payload, default=str)

        assert "## Provenance" in markdown
        assert "## Latency" in markdown
        assert "Trial" in markdown
        assert "All reconciliation figures match: no" in markdown

    def test_crash_record_does_not_break_the_report(self):
        result = _result(1)
        result.record = {}

        payload = build_json_report([result])

        assert payload["manifest"] == {}
        assert payload["cases"][0]["outcome"]["status"] is None
        assert "## Latency" in render_markdown([result])


class TestParser:
    def test_trials_and_notes(self):
        args = build_parser().parse_args(["--trials", "3", "--notes", "x"])
        assert args.trials == 3
        assert args.notes == "x"

    def test_defaults(self):
        args = build_parser().parse_args([])
        assert args.trials == 1
        assert args.notes == ""

    def test_zero_trials_rejected(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["--trials", "0"])


class TestReconcileTyped:
    def test_date_and_string_match(self):
        import datetime

        rows = mf.reconcile(
            {"fact_sales": {"min_sale_date": "2025-01-02"}},
            {"fact_sales": {"min_sale_date": datetime.date(2025, 1, 2)}},
        )
        assert rows[0]["match"] is True

    def test_float_noise_matches_money_string(self):
        rows = mf.reconcile(
            {"fact_sales": {"sum_net_revenue": "30.60"}},
            {"fact_sales": {"sum_net_revenue": 30.599999999999998}},
        )
        assert rows[0]["match"] is True

    def test_none_on_either_side_is_false(self):
        assert not mf.reconcile(
            {"fact_sales": {"row_count": None}},
            {"fact_sales": {"row_count": 3}},
        )[0]["match"]
        assert not mf.reconcile(
            {"fact_sales": {"row_count": 3}},
            {"fact_sales": {"row_count": None}},
        )[0]["match"]

    def test_unparseable_never_raises(self):
        rows = mf.reconcile(
            {"fact_sales": {"row_count": 3}},
            {"fact_sales": {"row_count": "abc"}},
        )
        assert rows[0]["match"] is False
        assert rows[0]["note"]


class TestManifestHardening:
    def test_git_hashes_head_diff_and_untracked(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()

        def git(*args):
            subprocess.run(
                ["git", "-c", "user.email=a@b.c", "-c", "user.name=t", *args],
                cwd=repo, check=True, capture_output=True,
            )

        git("init", "-q")
        (repo / "a.txt").write_text("one\n")
        git("add", ".")
        git("commit", "-q", "-m", "init")

        clean = mf.git_identity(repo)
        (repo / "new.txt").write_text("fresh\n")
        (repo / "a.txt").write_text("two\n")
        git("add", "a.txt")  # staged change is still in `git diff HEAD`
        dirty = mf.git_identity(repo)

        assert dirty["diff_sha256"] != clean["diff_sha256"]
        assert list(dirty["untracked_sha256"]) == ["new.txt"]
        assert dirty["tree_sha256"] != clean["tree_sha256"]

    def test_a_failing_section_is_isolated(self, csv_dir, tmp_path):
        service = FakeBigQueryService(rows=[TestLiveAggregates()._live_row()])
        pipeline = SQLExecutionPipeline(bigquery_service=service)

        manifest = mf.build_manifest(
            repo_root=tmp_path,
            bigquery_service=service,
            execution_pipeline=pipeline,
            csv_dir=tmp_path / "no_such_dir",
            case_paths=[tmp_path / "missing.yaml"],
            model_identity={"tag": "m"},
            trials=1,
        )

        assert "error" not in manifest
        assert manifest["dataset"]["all_match"] is False
        assert "error" in manifest["dataset"]["csv_aggregates"]
        assert "error" in manifest["cases"]
        assert "error" in manifest["git"]
        assert "packages" in manifest["versions"]
        assert manifest["versions"]["registry_version"] is None
        assert manifest["config"]["MAX_QUERY_BYTES"]
        # And the report still renders from a partial manifest.
        markdown = render_markdown([_result(1)], manifest=manifest)
        assert "## Provenance" in markdown
        assert "WARNING" in markdown

    def test_error_manifest_is_rendered(self):
        markdown = render_markdown(
            [_result(1)], manifest={"error": "boom", "model": {"tag": "m"}}
        )
        assert "Manifest unavailable: boom" in markdown

    def test_digest_match_normalises_latest(self, monkeypatch):
        import sys
        import types

        class Client:
            def __init__(self, host=None, timeout=None):
                pass

            def show(self, name):
                return {"details": {"family": "gemma"}}

            def list(self):
                return {"models": [{"name": "gemma4:latest",
                                    "digest": "d1"}]}

            def ps(self):
                return {"models": []}

        fake = types.SimpleNamespace(Client=Client)
        monkeypatch.setitem(sys.modules, "ollama", fake)

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"version": "0.9.1"}'

        monkeypatch.setattr(
            mf.urllib.request, "urlopen", lambda url, timeout=None: Resp()
        )

        found = mf._default_ollama_provider("gemma4", "http://h/", 1.0)
        assert found["digest"] == "d1"
        assert found["server_version"] == "0.9.1"
        assert "digest_missing" not in found

        missing = mf._default_ollama_provider("other", "http://h/", 1.0)
        assert missing["digest_missing"] is True


class TestSummaryHonesty:
    def test_pooled_and_per_trial_denominators(self):
        from evaluation.scorers import ScoreResult

        first = _result(1)
        second = _result(2)
        second.scores.add(ScoreResult("executed", False, "nope"))

        payload = build_json_report([first, second])
        summary = payload["summary"]

        assert summary["cases"] == 2
        assert summary["passed"] == 1
        by_trial = {e["trial"]: e for e in summary["by_trial"]}
        assert by_trial[1]["passed"] == 1
        assert by_trial[2]["passed"] == 0
        # Mean includes both durations (1000 and 2000 ms).
        assert summary["latency_ms"]["mean"] == 1500.0
        assert summary["latency_ms"]["median"] == 1500.0
        assert summary["warehouse"] == {
            "final_execution_bytes_billed": 0,
            "final_execution_bytes_processed": 0,
            "final_executions_cache_hits": 0,
            "sql_executions": 0,
        }
        assert summary["failures"] == {
            "rejected": 0, "error": 0, "unavailable": 0,
        }
        assert summary["execution_coverage"] == {
            "executed": 0, "attempted": 2,
        }
        markdown = render_markdown([first, second])
        assert "### Supplementary (not part of the pass rate)" in markdown
        assert "denominators exclude failed/rejected runs" in markdown


PERTH_SYDNEY_SHA = (
    "b61edbab7e0ced79690546efe7353510e9db11037f1fc5fe8ddbf6a7ca43c484"
)
NSW_WA_SHA = (
    "8cef7b163d5e4d98dd95d10152b2039515e9de384b7281ec018f1a6ef500f22c"
)


def _full_csv_dir(tmp_path):
    """A CSV dir with every categorical column of every table."""

    d = tmp_path / "full"
    d.mkdir()
    for table, columns in mf.CATEGORICAL_COLUMNS.items():
        header = ",".join(columns)
        rows = []
        for i in (1, 2):
            rows.append(",".join(f"{c}{i}" for c in columns))
        (d / f"{table}.csv").write_text(header + "\n" + "\n".join(rows) + "\n")
    return d


def _pipeline_serving(csv_columns, *, tamper=None):
    """A pipeline whose single canned row per table mirrors ``csv_columns``."""

    service = FakeBigQueryService()
    original = service.run_query

    def patched(sql, **kwargs):
        table = next(t for t in csv_columns if f".{t}`" in sql)
        row = {}
        for column, fp in csv_columns[table].items():
            row[f"{column}__distinct"] = fp["distinct"]
            row[f"{column}__sha256"] = fp["sha256"].upper()
        if tamper and tamper[0] == table:
            row[f"{tamper[1]}__sha256"] = "0" * 64
        service.rows = [row]
        return original(sql, **kwargs)

    service.run_query = patched  # type: ignore[method-assign]
    return service, SQLExecutionPipeline(bigquery_service=service)


class TestCategoricalColumns:
    def test_columns_cover_the_dataset_headers(self):
        import csv as csvlib
        from pathlib import Path

        datasets = Path(__file__).resolve().parents[2] / "Datasets"
        numeric = {"quantity", "unit_price", "discount_pct", "gross_revenue",
                   "discount_amount", "net_revenue", "tax_amount",
                   "total_price", "cost_amount", "profit_amount",
                   "reward_points", "unit_cost",
                   "list_price"}
        for table, columns in mf.CATEGORICAL_COLUMNS.items():
            with (datasets / f"{table}.csv").open(newline="") as handle:
                header = next(csvlib.reader(handle))
            assert set(columns) == set(header) - numeric
            assert len(columns) == len(set(columns))

    def test_csv_fingerprint_is_hand_computed(self, tmp_path):
        d = tmp_path / "tiny"
        d.mkdir()
        (d / "fact_sales.csv").write_text(
            "branch_city,branch_state\n"
            "Sydney,NSW\nPerth,WA\nSydney,NSW\n,\n"
        )

        prints = mf.csv_column_fingerprints(
            d, {"fact_sales": ["branch_city", "branch_state"]}
        )

        assert prints == {
            "fact_sales": {
                "branch_city": {"distinct": 2, "sha256": PERTH_SYDNEY_SHA},
                "branch_state": {"distinct": 2, "sha256": NSW_WA_SHA},
            }
        }

    def test_missing_csv_is_recorded_per_table(self, tmp_path):
        prints = mf.csv_column_fingerprints(tmp_path, {"fact_sales": ["x"]})
        assert "error" in prints["fact_sales"]

    def test_live_runs_one_query_per_table_through_the_pipeline(
        self, tmp_path
    ):
        csv_columns = mf.csv_column_fingerprints(_full_csv_dir(tmp_path))
        service, pipeline = _pipeline_serving(csv_columns)

        live = mf.live_column_fingerprints(pipeline, data.DATASET_PATH)

        assert len(service.run_query_calls) == 3
        assert len(service.dry_run_calls) == 3
        # The pipeline pretty-prints the SQL; compare without whitespace.
        sql = "".join(
            next(
                c for c in service.run_query_calls if "fact_sales" in c
            ).split()
        )
        for fragment in (
            "COUNT(DISTINCT branch_city) AS branch_city__distinct",
            "TO_HEX(SHA256(STRING_AGG(DISTINCT CAST(branch_city AS STRING), "
            "'|' ORDER BY CAST(branch_city AS STRING)))) AS "
            "branch_city__sha256",
        ):
            assert "".join(fragment.split()) in sql
        assert live["fact_sales"]["branch_city"] == csv_columns[
            "fact_sales"
        ]["branch_city"]
        assert live["fact_sales"]["evidence"]["job_id"] == "fake-job-1"
        assert live["fact_sales"]["evidence"]["run_id"]
        assert all(
            r["match"] for r in mf.reconcile_columns(csv_columns, live)
        )

    def test_failure_is_recorded_not_raised(self, tmp_path):
        service = FakeBigQueryService(run_query_error=RuntimeError("boom"))
        pipeline = SQLExecutionPipeline(bigquery_service=service)

        live = mf.live_column_fingerprints(pipeline, data.DATASET_PATH)

        assert all("boom" in entry["error"] for entry in live.values())
        csv_columns = mf.csv_column_fingerprints(_full_csv_dir(tmp_path))
        rows = mf.reconcile_columns(csv_columns, live)
        assert rows and not any(r["match"] for r in rows)

    def test_one_mismatching_column_breaks_all_match(self, tmp_path):
        d = _full_csv_dir(tmp_path)
        csv_columns = mf.csv_column_fingerprints(d)
        service, pipeline = _pipeline_serving(
            csv_columns, tamper=("fact_sales", "branch_city")
        )

        manifest = mf.build_manifest(
            repo_root=tmp_path,
            bigquery_service=service,
            execution_pipeline=pipeline,
            csv_dir=d,
            case_paths=[],
            model_identity={"tag": "m"},
            trials=1,
        )

        # Column rows are in the reconciliation; the numeric side cannot
        # match the canned rows here, so check the column rows directly.
        rows = [
            r for r in manifest["dataset"]["reconciliation"]
            if r["figure"].startswith(("distinct:", "sha256:"))
        ]
        bad = {(r["table"], r["figure"]) for r in rows if not r["match"]}
        assert bad == {("fact_sales", "sha256:branch_city")}
        assert manifest["dataset"]["all_match"] is False
        assert manifest["dataset"]["csv_columns"] == csv_columns
        assert "branch_city" in manifest["dataset"]["live_columns"][
            "fact_sales"
        ]

        markdown = render_markdown(
            [_result(1)], manifest=manifest
        )
        assert "**Mismatching columns:** fact_sales.branch_city" in markdown
        assert (
            "| Table | Column | Distinct (CSV/BQ) | Values match |" in markdown
        )

    def test_missing_live_figure_is_a_mismatch(self):
        csv_columns = {"t": {"c": {"distinct": 1, "sha256": "ab"}}}
        rows = mf.reconcile_columns(csv_columns, {})
        assert [r["match"] for r in rows] == [False, False]


class TestExtraFiles:
    def test_annex_is_hashed_raw(self, csv_dir, tmp_path):
        annex = tmp_path / "golden_expected_facts.yaml"
        annex.write_bytes(b"a: 1\n")
        service = FakeBigQueryService()
        manifest = mf.build_manifest(
            repo_root=tmp_path,
            bigquery_service=service,
            execution_pipeline=SQLExecutionPipeline(bigquery_service=service),
            csv_dir=csv_dir,
            case_paths=[],
            model_identity={"tag": "m"},
            trials=1,
            extra_files=[annex],
        )
        import hashlib

        assert manifest["files"] == {
            "golden_expected_facts.yaml": {
                "sha256": hashlib.sha256(b"a: 1\n").hexdigest(),
                "size": 5,
            }
        }


class TestReportProvenanceExtras:
    def test_warm_up_run_log_loaded_models_and_summary_lines(self):
        manifest = {
            "dataset": {"reconciliation": [], "all_match": True},
            "trials": {
                "warm_up": {"question": "hello", "status": "success",
                            "duration_ms": 2500.0},
                "run_log_file": "/tmp/runs.jsonl",
                "loaded_before_trial": {"1": [{"model": "gemma4:latest"}],
                                        "2": []},
            },
        }
        markdown = render_markdown([_result(1)], manifest=manifest)

        assert "Warm-up: question `hello` · status success · 2.5s" in markdown
        assert "/tmp/runs.jsonl" in markdown
        assert "| 1 | `gemma4:latest` | yes |" in markdown
        assert "| 2 | n/a | no |" in markdown
        assert "Failures: rejected: 0, error: 0, unavailable: 0" in markdown
        assert "Warehouse (final execution per run)" in markdown
