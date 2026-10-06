"""Hermetic tests for the typed query intent, bounded compiler and result binder (Phase 3)."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
import sqlglot
import yaml
from sqlglot import exp

from src.semantic_query import (
    QUESTION_SHAPE_COVERAGE,
    ClarificationRequired,
    CompiledQuery,
    CompileRejection,
    IntentRejection,
    QueryIntent,
    ResultBindingError,
    bind_result,
    compile_query,
    parse_query_intent,
)
from src.semantics.registry import MetricRegistry, load_registry
from src.sql_execution_pipeline import SQLExecutionPipeline
from src.sql_validator import validate_read_only_sql, validate_table_access
from tests.fakes import data
from tests.fakes.bigquery import FakeBigQueryService

YEAR_2025 = {"dimension_id": "sale_date", "op": "between", "values": ["2025-01-01", "2025-12-31"]}


@pytest.fixture(scope="module")
def registry() -> MetricRegistry:
    return load_registry()


def intent_json(**overrides: Any) -> str:
    body: dict[str, Any] = {"metric_id": "total_net_sales", "dimensions": [], "filters": [YEAR_2025],
                            "date_grain": None, "order": [], "limit": None, "request_id": "req-1"}
    body.update(overrides)
    return json.dumps(body)


def compile_ok(registry: MetricRegistry, **overrides: Any) -> CompiledQuery:
    intent = parse_query_intent(intent_json(**overrides), registry)
    assert isinstance(intent, QueryIntent), intent
    compiled = compile_query(intent, registry, project_id=data.PROJECT_ID, dataset_id=data.DATASET_ID)
    assert isinstance(compiled, CompiledQuery), compiled
    return compiled


def assert_passes_validators(sql: str, registry: MetricRegistry) -> None:
    assert validate_read_only_sql(sql).is_valid
    access = validate_table_access(sql, allowed_project_id=data.PROJECT_ID, allowed_dataset_id=data.DATASET_ID,
                                   allowed_table_names=set(registry.primary_keys))
    assert access.is_valid, access.message
    assert "*" not in sql.replace(" * 100", "")  # only the registry constant multiply


# ---------------------------------------------------------------- intent parsing

class TestParseIntent:
    def test_accepts_well_formed_intent(self, registry):
        intent = parse_query_intent(intent_json(dimensions=["category"], limit=5,
                                                order=[{"field": "total_net_sales", "direction": "desc"}]), registry)
        assert isinstance(intent, QueryIntent)
        assert intent.dimensions == ("category",)
        assert intent.filters[0].values == ("2025-01-01", "2025-12-31")
        assert intent.order == (("total_net_sales", "desc"),)

    @pytest.mark.parametrize("text, code", [
        ("not json", "invalid_json"),
        ("[1]", "not_an_object"),
        (intent_json(extra="x"), "unknown_fields"),
        (json.dumps({"metric_id": "total_net_sales", "request_id": "r"}), "missing_fields"),
        (intent_json(metric_id="average_price"), "unknown_metric"),
        (intent_json(metric_id="made_up_metric"), "unknown_metric"),
        (intent_json(dimensions=["planet"]), "unknown_dimension"),
        (intent_json(metric_id="list_price", dimensions=["branch_city"]), "dimension_not_allowed"),
        (intent_json(filters=[{"dimension_id": "category", "op": "eq", "values": [1]}]), "non_string_filter_value"),
        (intent_json(filters=[{"dimension_id": "category", "op": "like", "values": ["x"]}]), "invalid_filter"),
        (intent_json(filters=[{"dimension_id": "category", "op": "eq", "values": ["x"], "sql": "1"}]),
         "invalid_filter"),
        (intent_json(date_grain="week"), "invalid_date_grain"),
        (intent_json(order=[{"field": "x", "direction": "sideways"}]), "invalid_order"),
        (intent_json(limit=10_000), "limit_out_of_range"),
        (intent_json(limit=0), "limit_out_of_range"),
        (intent_json(limit=True), "invalid_field_type"),
        (json.dumps({"ambiguous_phrase": "average thing", "request_id": "r"}), "unknown_ambiguous_phrase"),
        (json.dumps({"ambiguous_phrase": "average price", "request_id": "r", "metric_id": "x"}), "unknown_fields"),
    ])
    def test_rejections(self, registry, text, code):
        result = parse_query_intent(text, registry)
        assert isinstance(result, IntentRejection)
        assert result.reason_code == code

    def test_limit_cap_read_from_config_at_call_time(self, registry, monkeypatch):
        from src import config
        monkeypatch.setattr(config, "MAX_RESULT_ROWS", 3)
        result = parse_query_intent(intent_json(limit=4), registry)
        assert isinstance(result, IntentRejection) and result.reason_code == "limit_out_of_range"

    def test_average_price_requires_clarification(self, registry):
        result = parse_query_intent(json.dumps({"ambiguous_phrase": "Average  Price", "request_id": "r"}), registry)
        assert isinstance(result, ClarificationRequired)
        assert set(result.candidates) == {"average_listing_price_per_product", "mean_line_unit_price",
                                          "quantity_weighted_mean_unit_price", "average_realised_net_price_per_unit"}
        assert result.request_id == "r"


# ---------------------------------------------------------------- compiler: golden shapes

GOLDEN_SHAPES: dict[str, tuple[dict[str, Any], list[str]]] = {
    "total_net_sales_2025": ({}, ["SUM(f.net_revenue) AS total_net_sales"]),
    "net_sales_by_month_2025": ({"date_grain": "month"},
                                ["FORMAT_DATE('%Y-%m', f.sale_date) AS sale_month", "GROUP BY sale_month"]),
    "profit_by_category_2025": ({"metric_id": "total_profit", "dimensions": ["category"], "limit": 1,
                                 "order": [{"field": "total_profit", "direction": "desc"}]},
                                ["SUM(f.profit_amount)", "JOIN `test-project.business_insights.dim_products` AS p "
                                 "ON f.product_id = p.product_id", "ORDER BY total_profit DESC", "LIMIT 1"]),
    "top_5_products_by_revenue_2025": ({"dimensions": ["product_name"], "limit": 5,
                                        "order": [{"field": "total_net_sales", "direction": "desc"}]},
                                       ["p.product_name AS product_name", "LIMIT 5"]),
    "revenue_by_region_2025": ({"dimensions": ["branch_region"]}, ["f.branch_region AS branch_region"]),
    "sales_by_membership_status_2025": ({"dimensions": ["membership_status"]},
                                        ["JOIN `test-project.business_insights.dim_customers` AS c "
                                         "ON f.customer_id = c.customer_id", "c.membership_status"]),
    "transaction_count_2025": ({"metric_id": "transaction_line_count"}, ["COUNT(f.sale_id)"]),
    "average_discount_by_promotion_2025": ({"metric_id": "mean_line_discount_pct", "dimensions": ["promotion_type"]},
                                           ["AVG(f.discount_pct) AS mean_line_discount_pct"]),
    "profit_margin_by_category_2025": ({"metric_id": "profit_margin_pct", "dimensions": ["category"]},
                                       ["(SAFE_DIVIDE(SUM(f.profit_amount), SUM(f.net_revenue)) * 100)"]),
    "quantity_sold_by_category_2025": ({"metric_id": "total_units", "dimensions": ["category"]},
                                       ["SUM(f.quantity) AS total_units"]),
    "electronics_november_december_2025": (
        {"filters": [{"dimension_id": "category", "op": "eq", "values": ["Electronics"]},
                     {"dimension_id": "sale_date", "op": "between", "values": ["2025-11-01", "2025-12-31"]}]},
        ["p.category = 'Electronics'", "f.sale_date BETWEEN DATE('2025-11-01') AND DATE('2025-12-31')"]),
    "best_month_for_profit_2025": ({"metric_id": "total_profit", "date_grain": "month", "limit": 1,
                                    "order": [{"field": "total_profit", "direction": "desc"}]},
                                   ["ORDER BY total_profit DESC"]),
}


class TestCompileGoldenShapes:
    @pytest.mark.parametrize("case_id", sorted(GOLDEN_SHAPES))
    def test_compiles_and_passes_validators(self, registry, case_id):
        overrides, fragments = GOLDEN_SHAPES[case_id]
        compiled = compile_ok(registry, **overrides)
        for fragment in fragments:
            assert fragment in compiled.sql, (fragment, compiled.sql)
        assert "SELECT *" not in compiled.sql
        assert_passes_validators(compiled.sql, registry)
        assert QUESTION_SHAPE_COVERAGE[case_id]["status"] in {"supported", "clarification_required"}

    def test_year_filter_uses_date_literals(self, registry):
        assert "f.sale_date BETWEEN DATE('2025-01-01') AND DATE('2025-12-31')" in compile_ok(registry).sql

    def test_year_grain_and_distinct_count(self, registry):
        sql = compile_ok(registry, metric_id="distinct_customer_count", date_grain="year").sql
        assert "EXTRACT(YEAR FROM f.sale_date) AS sale_year" in sql
        assert "COUNT(DISTINCT f.customer_id)" in sql

    def test_effective_discount_rate_is_ratio_of_sums(self, registry):
        sql = compile_ok(registry, metric_id="effective_discount_rate").sql
        assert "SAFE_DIVIDE(SUM(f.discount_amount), SUM(f.gross_revenue))" in sql

    def test_coverage_catalog_covers_all_golden_cases(self):
        with open("evaluation/golden_cases.yaml", encoding="utf-8") as handle:
            cases = yaml.safe_load(handle)
        case_ids = {c["case_id"] for c in (cases["cases"] if isinstance(cases, dict) else cases)}
        assert len(case_ids) == 15
        assert set(QUESTION_SHAPE_COVERAGE) == case_ids
        assert all(v["status"] in {"supported", "unsupported", "clarification_required"}
                   for v in QUESTION_SHAPE_COVERAGE.values())
        assert QUESTION_SHAPE_COVERAGE["average_discount_by_promotion_2025"]["status"] == "clarification_required"

    def test_every_supported_catalog_entry_compiles_and_validates(self, registry):
        assert set(CATALOG_INTENTS) == set(QUESTION_SHAPE_COVERAGE)
        assert len(CATALOG_INTENTS) == 15
        for case_id, entry in QUESTION_SHAPE_COVERAGE.items():
            if entry["status"] not in {"supported", "clarification_required"}:
                continue
            overrides, question = CATALOG_INTENTS[case_id]
            parsed = parse_query_intent(intent_json(**overrides), registry, question=question,
                                        resolved_ambiguities=("average discount",))
            assert isinstance(parsed, QueryIntent), (case_id, parsed)
            compiled = compile_query(parsed, registry, project_id=data.PROJECT_ID, dataset_id=data.DATASET_ID)
            assert isinstance(compiled, CompiledQuery), (case_id, compiled)
            assert_passes_validators(compiled.sql, registry)

    @pytest.mark.parametrize("case_id", ["total_net_sales_2025", "profit_margin_by_category_2025"])
    def test_accepted_by_execution_pipeline_stages(self, registry, case_id):
        compiled = compile_ok(registry, **GOLDEN_SHAPES[case_id][0])
        fake = FakeBigQueryService(rows=[{"total_net_sales": Decimal("1.00")}])
        result = SQLExecutionPipeline(bigquery_service=fake).execute(compiled.sql)
        assert result["status"] == "success", result
        assert len(fake.run_query_calls) == 1


class TestCompileRejections:
    @pytest.mark.parametrize("metric_id", ["average_group_total_sold_groups", "average_group_total_full_population",
                                           "share_of_total", "percentage_change", "difference", "quantity",
                                           "average_listing_price_per_product", "discounted_unit_price"])
    def test_unsupported_metrics_rejected_without_fallback(self, registry, metric_id):
        intent = parse_query_intent(intent_json(metric_id=metric_id, filters=[]), registry)
        assert isinstance(intent, QueryIntent)
        result = compile_query(intent, registry, project_id=data.PROJECT_ID, dataset_id=data.DATASET_ID)
        assert isinstance(result, CompileRejection)
        assert result.reason_code == "unsupported_shape"

    @pytest.mark.parametrize("overrides", [
        {"dimensions": ["category", "brand"]},
        {"dimensions": ["category"], "date_grain": "month"},
        {"order": [{"field": "total_net_sales", "direction": "desc"}]},  # order without grouping
        {"dimensions": ["category"], "order": [{"field": "made_up_alias", "direction": "asc"}]},
        {"filters": [{"dimension_id": "sale_date", "op": "between", "values": ["2025-12-31", "2025-01-01"]}]},
        {"filters": [{"dimension_id": "sale_date", "op": "eq", "values": ["2025-13-01"]}]},
        {"filters": [{"dimension_id": "sale_date", "op": "eq", "values": ["2025-01-01') OR (1=1"]}]},
        {"filters": [{"dimension_id": "category", "op": "between", "values": ["a", "b"]}]},
        {"filters": [{"dimension_id": "category", "op": "eq", "values": ["a\nb"]}]},
        {"dimensions": ["sale_date"]},
    ])
    def test_unsupported_shapes(self, registry, overrides):
        intent = parse_query_intent(intent_json(**overrides), registry)
        assert isinstance(intent, QueryIntent), intent
        result = compile_query(intent, registry, project_id=data.PROJECT_ID, dataset_id=data.DATASET_ID)
        assert isinstance(result, CompileRejection), getattr(result, "sql", result)


class TestInjectionAndBindings:
    def test_quote_injection_is_escaped_into_one_literal(self, registry):
        hostile = "x' OR 1=1; DROP TABLE t; -- \\"
        compiled = compile_ok(registry, filters=[{"dimension_id": "category", "op": "in",
                                                  "values": [hostile, "Toys"]}])
        assert_passes_validators(compiled.sql, registry)
        tree = sqlglot.parse_one(compiled.sql, read="bigquery")
        literals = [lit.this for lit in tree.find_all(exp.Literal) if lit.is_string]
        assert hostile in literals and "Toys" in literals
        assert not list(tree.find_all(exp.Or))

    def test_bindings_come_from_plan_not_aliases(self, registry):
        compiled = compile_ok(registry, metric_id="total_profit", dimensions=["category"])
        assert [(b.binding_id, b.kind, b.ref_id) for b in compiled.bindings] == [
            ("category", "dimension", "category"), ("total_profit", "metric", "total_profit")]
        metric_binding = compiled.bindings[-1]
        assert metric_binding.unit == "money"
        assert metric_binding.currency == load_registry().currency
        assert metric_binding.grain == "category"
        # A model-supplied alias cannot become a binding or order key.
        intent = parse_query_intent(intent_json(dimensions=["category"],
                                                order=[{"field": "average_price", "direction": "desc"}]), registry)
        assert isinstance(intent, QueryIntent)
        assert isinstance(compile_query(intent, registry, project_id="p", dataset_id="d"), CompileRejection)

    def test_plan_id_is_stable_and_ignores_request_id(self, registry):
        a = compile_ok(registry, request_id="a").plan_id
        assert a == compile_ok(registry, request_id="b").plan_id
        assert a != compile_ok(registry, dimensions=["category"]).plan_id
        assert len(a) == 64


# ---------------------------------------------------------------- result binder

def execution(rows: list[dict[str, Any]], *, total: int | None = None, truncated: bool = False) -> dict[str, Any]:
    return {"status": "success", "run_id": "run-1", "job_id": "job-1", "rows": rows, "row_count": len(rows),
            "total_result_rows": len(rows) if total is None else total, "result_truncated_by_client": truncated}


class TestIntentGuards:
    QUESTION = "What was the average discount by promotion type in 2025?"
    DISCOUNT = {"metric_id": "mean_line_discount_pct", "dimensions": ["promotion_type"]}

    def test_ambiguous_phrase_in_question_requires_clarification(self, registry):
        result = parse_query_intent(intent_json(**self.DISCOUNT), registry, question=self.QUESTION)
        assert isinstance(result, ClarificationRequired)
        assert result.phrase == "average discount" and "mean_line_discount_pct" in result.candidates
        assert result.request_id == "req-1"

    def test_resolved_ambiguity_and_empty_question_parse(self, registry):
        resolved = parse_query_intent(intent_json(**self.DISCOUNT), registry, question=self.QUESTION,
                                      resolved_ambiguities=("average discount",))
        assert isinstance(resolved, QueryIntent)
        assert isinstance(parse_query_intent(intent_json(**self.DISCOUNT), registry), QueryIntent)

    def test_request_id_mismatch_rejected(self, registry):
        bad = parse_query_intent(intent_json(), registry, expected_request_id="req-2")
        assert isinstance(bad, IntentRejection) and bad.reason_code == "request_id_mismatch"
        assert isinstance(parse_query_intent(intent_json(), registry, expected_request_id="req-1"), QueryIntent)

    @pytest.mark.parametrize("value", ["2025-01-01\n", "２０２５-01-01"])
    def test_date_filter_must_fullmatch_ascii(self, registry, value):
        intent = parse_query_intent(intent_json(filters=[{"dimension_id": "sale_date", "op": "eq",
                                                          "values": [value]}]), registry)
        assert isinstance(intent, QueryIntent)
        assert isinstance(compile_query(intent, registry, project_id="p", dataset_id="d"), CompileRejection)

    def test_decimal_constant_pattern_is_fullmatch_ascii(self):
        from src.semantic_query import _DECIMAL_LITERAL
        assert _DECIMAL_LITERAL.fullmatch("100") and _DECIMAL_LITERAL.fullmatch("-1.5")
        assert not _DECIMAL_LITERAL.fullmatch("100\n") and not _DECIMAL_LITERAL.fullmatch("１００")


TOTAL_2025 = {}
CATALOG_INTENTS: dict[str, tuple[dict[str, Any], str]] = {
    "net_sales_by_month_2025": ({"date_grain": "month"}, ""),
    "total_net_sales_2025": (TOTAL_2025, ""),
    "total_profit_2025": ({"metric_id": "total_profit"}, ""),
    "profit_by_category_2025": (GOLDEN_SHAPES["profit_by_category_2025"][0], ""),
    "top_5_products_by_revenue_2025": (GOLDEN_SHAPES["top_5_products_by_revenue_2025"][0], ""),
    "revenue_by_region_2025": ({"dimensions": ["branch_region"]}, ""),
    "electronics_november_december_2025": (GOLDEN_SHAPES["electronics_november_december_2025"][0], ""),
    "sales_by_membership_status_2025": ({"dimensions": ["membership_status"]}, ""),
    "transaction_count_2025": ({"metric_id": "transaction_line_count"}, ""),
    "average_discount_by_promotion_2025": (GOLDEN_SHAPES["average_discount_by_promotion_2025"][0],
                                           "What was the average discount by promotion type in 2025?"),
    "profit_margin_by_category_2025": ({"metric_id": "profit_margin_pct", "dimensions": ["category"]}, ""),
    "sales_by_channel_2025": ({"dimensions": ["sales_channel"]}, ""),
    "quantity_sold_by_category_2025": ({"metric_id": "total_units", "dimensions": ["category"]}, ""),
    "best_month_for_profit_2025": (GOLDEN_SHAPES["best_month_for_profit_2025"][0], ""),
    "no_sales_in_1999": ({"date_grain": "month",
                          "filters": [{"dimension_id": "sale_date", "op": "between",
                                       "values": ["1999-01-01", "1999-12-31"]}]}, ""),
}


class TestBindResult:
    def bind(self, registry, compiled, result, **kw):
        result = {"executed_sql": compiled.sql, **result}
        return bind_result(compiled, result, request_id="req-1", question="Q?",
                           registry_version=registry.registry_version, **kw)

    def test_scalar_is_complete(self, registry):
        manifest = self.bind(registry, compile_ok(registry), execution([{"total_net_sales": Decimal("5.00")}]))
        assert manifest.completeness == "complete"
        assert manifest.completeness_basis == "scalar_aggregate"
        assert manifest.row_refs == {"r0": {"total_net_sales": Decimal("5.00")}}
        assert manifest.execution_id == "run-1" and manifest.job_id == "job-1"
        assert manifest.metric_ids == ("total_net_sales",)
        assert manifest.output_schema == ("total_net_sales",)

    def test_grouped_complete_when_all_groups_below_cap(self, registry):
        rows = [{"branch_region": r, "total_net_sales": Decimal("1")} for r in ("East", "West")]
        manifest = self.bind(registry, compile_ok(registry, dimensions=["branch_region"]), execution(rows))
        assert manifest.completeness == "complete"
        assert list(manifest.row_refs) == ["r0", "r1"]

    def test_grouped_at_cap_is_unknown(self, registry):
        rows = [{"branch_region": r, "total_net_sales": Decimal("1")} for r in ("East", "West")]
        manifest = self.bind(registry, compile_ok(registry, dimensions=["branch_region"]), execution(rows),
                             max_result_rows=2)
        assert manifest.completeness == "unknown"

    def test_truncated_is_subset(self, registry):
        rows = [{"branch_region": "East", "total_net_sales": Decimal("1")}]
        compiled = compile_ok(registry, dimensions=["branch_region"])
        assert self.bind(registry, compiled, execution(rows, truncated=True)).completeness == "subset"
        manifest = self.bind(registry, compiled, execution(rows, total=4))
        assert (manifest.completeness, manifest.completeness_basis) == ("subset", "total_result_rows > row_count")

    def test_limited_top_n_is_unknown(self, registry):
        compiled = compile_ok(registry, dimensions=["category"], limit=1,
                              order=[{"field": "total_net_sales", "direction": "desc"}])
        manifest = self.bind(registry, compiled, execution([{"category": "Toys", "total_net_sales": Decimal("1")}]))
        assert (manifest.completeness, manifest.completeness_basis) == ("unknown", "query_limit_applied")

    def test_columns_must_match_plan_bindings(self, registry):
        with pytest.raises(ResultBindingError):
            self.bind(registry, compile_ok(registry), execution([{"average_price": Decimal("1")}]))

    def test_failed_execution_cannot_be_bound(self, registry):
        with pytest.raises(ResultBindingError):
            self.bind(registry, compile_ok(registry), {"status": "rejected", "rows": []})

    def test_registry_version_must_match(self, registry):
        with pytest.raises(ResultBindingError):
            bind_result(compile_ok(registry), execution([{"total_net_sales": Decimal("1")}]),
                        request_id="r", question="q", registry_version="0.0.0")

    def test_mismatched_executed_sql_rejected(self, registry):
        compiled = compile_ok(registry)
        result = {**execution([{"total_net_sales": Decimal("5.00")}]), "executed_sql": "SELECT 999 AS total_net_sales"}
        with pytest.raises(ResultBindingError, match="executed_sql does not match"):
            bind_result(compiled, result, request_id="r", question="q",
                        registry_version=registry.registry_version)
        missing = execution([{"total_net_sales": Decimal("5.00")}])
        with pytest.raises(ResultBindingError):
            bind_result(compiled, missing, request_id="r", question="q",
                        registry_version=registry.registry_version)

    def test_pipeline_limited_sql_accepted_and_hashed(self, registry):
        import hashlib
        compiled = compile_ok(registry, dimensions=["branch_region"])
        rows = [{"branch_region": "East", "total_net_sales": Decimal("1")}]
        result = SQLExecutionPipeline(bigquery_service=FakeBigQueryService(rows=rows)).execute(compiled.sql)
        assert result["executed_sql"] != compiled.sql  # LIMIT injected by the pipeline
        manifest = bind_result(compiled, result, request_id="r", question="q",
                               registry_version=registry.registry_version)
        assert manifest.executed_sql_sha256 == hashlib.sha256(str(result["executed_sql"]).encode()).hexdigest()

    def test_result_row_limit_from_execution_result_marks_cap_reached(self, registry):
        rows = [{"branch_region": r, "total_net_sales": Decimal("1")} for r in ("East", "West")]
        compiled = compile_ok(registry, dimensions=["branch_region"])
        result = {**execution(rows), "result_row_limit": 2, "limit_was_modified": True}
        manifest = self.bind(registry, compiled, result)
        assert (manifest.completeness, manifest.completeness_basis) == ("unknown", "row cap reached")
        result["limit_was_modified"] = False
        assert self.bind(registry, compiled, result).completeness_basis != "row cap reached"

    def test_binds_real_pipeline_output(self, registry):
        compiled = compile_ok(registry, date_grain="month")
        rows = [{"sale_month": "2025-01", "total_net_sales": Decimal("10.50")}]
        result = SQLExecutionPipeline(bigquery_service=FakeBigQueryService(rows=rows)).execute(compiled.sql)
        assert result["status"] == "success"
        manifest = self.bind(registry, compiled, result)
        assert manifest.completeness == "complete"
        assert manifest.execution_id == result["run_id"]
        assert manifest.result_id.startswith("result-")
