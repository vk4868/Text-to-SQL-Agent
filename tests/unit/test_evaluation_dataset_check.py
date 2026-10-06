"""Row-level dataset check: canonicalisation, swaps, live path, CLI."""

import hashlib
import json
from pathlib import Path

import pytest

import src.bigquery_service
import src.config
from evaluation import dataset_check as dc
from evaluation.dataset_check import (
    canonical_cell,
    canonical_numeric,
    csv_table_summary,
    live_table_sql,
    main,
    run_dataset_check,
)
from src.sql_execution_pipeline import SQLExecutionPipeline
from tests.fakes.bigquery import FakeBigQueryService

REAL = "acme-real-123"
REPO_CSV = Path(__file__).resolve().parents[2] / "Datasets"

SALES_HEADER = (
    "sale_id,sale_date,branch_id,branch_city,branch_state,branch_region,"
    "customer_id,product_id,quantity,unit_price,discount_pct,gross_revenue,"
    "discount_amount,net_revenue,tax_amount,total_price,cost_amount,"
    "profit_amount,reward_points,payment_method,sales_channel,promotion_type"
)


def sale(sale_id, city, customer="C1", product="P1"):
    return (
        f"{sale_id},2025-01-02,B1,{city},TX,South,{customer},{product},"
        "2,10.00,0.00,20.00,0.00,20.00,1.60,21.60,12.50,7.50,3,Card,Web,"
    )


def write_dataset(directory, cities=("Austin", "Dallas"), customer="C1"):
    directory.mkdir(parents=True, exist_ok=True)
    rows = [sale("S1", cities[0], customer), sale("S2", cities[1])]
    (directory / "fact_sales.csv").write_text(
        SALES_HEADER + "\n" + "\n".join(rows) + "\n"
    )
    (directory / "dim_customers.csv").write_text(
        "customer_id,customer_name,membership_status,customer_segment,"
        "gender,age_group,signup_date,home_city,home_state,"
        "acquisition_channel\n"
        "C1,Ann,Member,Retail,F,25-34,2024-01-01,Austin,TX,Web\n"
    )
    (directory / "dim_products.csv").write_text(
        "product_id,product_name,category,subcategory,brand,unit_cost,"
        "list_price,launch_date\n"
        "P1,Widget,Tools,Hand,Acme,1933.90,0.125,2023-05-01\n"
    )
    return directory


class TestCanonical:
    @pytest.mark.parametrize(
        "raw,expected",
        [("1933.90", "1933.9"), ("0.00", "0"), ("10.00", "10"),
         ("0.125", "0.125"), ("100", "100"), ("", ""), ("-0.00", "0")],
    )
    def test_numeric(self, raw, expected):
        assert canonical_numeric(raw) == expected

    def test_cells(self):
        assert canonical_cell("dim_products", "list_price", "10.00") == "10"
        assert canonical_cell("dim_products", "launch_date", "2023-05-01") == "2023-05-01"
        assert canonical_cell("dim_products", "brand", "Acme ") == "Acme "
        assert canonical_cell("dim_products", "brand", "") == ""
        assert canonical_cell("dim_products", "brand", None) == ""


class TestCsvFingerprint:
    def test_hand_computed(self, tmp_path):
        write_dataset(tmp_path)
        text = "P1\tWidget\tTools\tHand\tAcme\t1933.9\t0.125\t2023-05-01"
        summary = csv_table_summary(tmp_path, "dim_products")
        assert summary == {
            "row_count": 1,
            "distinct_keys": 1,
            "row_sha256": hashlib.sha256(text.encode()).hexdigest(),
        }

    def test_swapped_association_changes_fingerprint(self, tmp_path):
        a = write_dataset(tmp_path / "a", cities=("Austin", "Dallas"))
        b = write_dataset(tmp_path / "b", cities=("Dallas", "Austin"))
        sa = csv_table_summary(a, "fact_sales")
        sb = csv_table_summary(b, "fact_sales")
        assert sa["row_count"] == sb["row_count"]
        assert sa["row_sha256"] != sb["row_sha256"]

    def test_real_dataset_summarises(self):
        summary = csv_table_summary(REPO_CSV, "dim_products")
        assert summary["row_count"] == summary["distinct_keys"] > 0


def pick(canned, sql):
    """The pipeline reformats SQL, so match on table name, not exact text."""

    if "orphan_customers" in sql:
        return canned["fk"]
    for table in dc.KEYS:
        if "row_sha256" in sql and f".{table}`" in sql:
            return canned[table]
    raise AssertionError(sql)


def fake_pipeline(canned):
    """canned maps a SQL substring to the single row to return."""

    service = FakeBigQueryService()
    original = service.run_query

    def patched(sql, **kwargs):
        service.rows = [pick(canned, sql)]
        return original(sql, **kwargs)

    service.run_query = patched  # type: ignore[method-assign]
    return service, SQLExecutionPipeline(bigquery_service=service)


def canned_for(csv_dir, sha_override=None):
    canned = {}
    for table in dc.KEYS:
        expected = dict(csv_table_summary(csv_dir, table))
        if sha_override and table in sha_override:
            expected["row_sha256"] = sha_override[table]
        canned[table] = expected
    canned["fk"] = {"orphan_customers": 0, "orphan_products": 0}
    return canned


class TestLivePath:
    def dataset_path(self):
        return "test-project.business_insights"

    def test_match_through_pipeline(self, tmp_path):
        write_dataset(tmp_path)
        service, pipeline = fake_pipeline(canned_for(tmp_path))

        result = run_dataset_check(pipeline, self.dataset_path(), tmp_path)

        assert result["all_match"] is True
        assert all(result[t]["match"] for t in dc.KEYS)
        assert result["foreign_keys"]["match"] is True
        assert len(service.run_query_calls) == 4
        assert service.dry_run_calls
        assert "evidence" in result["fact_sales"]["bigquery"]

    def test_wrong_hash_mismatches(self, tmp_path):
        write_dataset(tmp_path)
        _, pipeline = fake_pipeline(
            canned_for(tmp_path, {"fact_sales": "0" * 64})
        )

        result = run_dataset_check(pipeline, self.dataset_path(), tmp_path)

        assert result["fact_sales"]["match"] is False
        assert result["dim_products"]["match"] is True
        assert result["all_match"] is False

    def test_orphans_fail(self, tmp_path):
        write_dataset(tmp_path)
        canned = canned_for(tmp_path)
        canned["fk"] = {"orphan_customers": 2, "orphan_products": 0}
        _, pipeline = fake_pipeline(canned)

        result = run_dataset_check(pipeline, self.dataset_path(), tmp_path)

        assert result["foreign_keys"]["match"] is False

    def test_csv_orphans_detected(self, tmp_path):
        write_dataset(tmp_path, customer="C9")
        assert dc.csv_orphans(tmp_path)["orphan_customers"] == 1

    def test_errors_recorded_not_raised(self, tmp_path):
        write_dataset(tmp_path)
        service = FakeBigQueryService(run_query_error=RuntimeError("boom"))
        pipeline = SQLExecutionPipeline(bigquery_service=service)

        result = run_dataset_check(pipeline, self.dataset_path(), tmp_path)

        assert result["all_match"] is False
        assert "error" in result["dim_products"]["bigquery"]

    def test_sql_shape(self):
        sql = live_table_sql("p.d", "dim_products", ["product_id", "brand"])
        assert "ORDER BY product_id" in sql
        assert "'\\t'" in sql and "'\\n'" in sql
        assert "`p.d.dim_products`" in sql


class TestCli:
    def _setup(self, monkeypatch, tmp_path, sha_override=None):
        csv_dir = write_dataset(tmp_path / "csv")
        service, _ = fake_pipeline(canned_for(csv_dir, sha_override))
        service.project_id = REAL
        monkeypatch.setattr(
            src.bigquery_service, "BigQueryService", lambda: service
        )
        monkeypatch.setattr(src.config, "PROJECT_ID", REAL)
        return csv_dir

    def test_main_ok(self, monkeypatch, tmp_path, capsys):
        csv_dir = self._setup(monkeypatch, tmp_path)
        out = tmp_path / "o" / "check.json"

        code = main(["--out", str(out), "--csv-dir", str(csv_dir)])

        assert code == 0
        text = out.read_text()
        assert REAL not in text
        assert json.loads(text)["all_match"] is True
        assert "dim_products: MATCH" in capsys.readouterr().out

    def test_main_mismatch(self, monkeypatch, tmp_path):
        csv_dir = self._setup(
            monkeypatch, tmp_path, {"dim_products": "f" * 64}
        )
        out = tmp_path / "check.json"

        code = main(["--out", str(out), "--csv-dir", str(csv_dir)])

        assert code == 1
        assert REAL not in out.read_text()
