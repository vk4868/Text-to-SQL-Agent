"""Hermetic tests for the semantic metric registry (src/semantics)."""

from __future__ import annotations

import csv
import dataclasses
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.semantics.registry import (
    Calculation,
    JoinDefinition,
    MetricRegistry,
    Operand,
    load_registry,
    validate_registry,
)

ROOT = Path(__file__).resolve().parents[3]
DATASETS = ROOT / "Datasets"
NUMERIC_COLUMNS = [
    "dim_products.list_price", "dim_products.unit_cost", "fact_sales.quantity",
    "fact_sales.unit_price", "fact_sales.discount_pct", "fact_sales.gross_revenue",
    "fact_sales.discount_amount", "fact_sales.net_revenue", "fact_sales.tax_amount",
    "fact_sales.total_price", "fact_sales.cost_amount", "fact_sales.profit_amount",
    "fact_sales.reward_points",
]


def _rows(table: str) -> list[dict[str, str]]:
    with open(DATASETS / f"{table}.csv", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def schema_columns() -> dict[str, list[str]]:
    columns: dict[str, list[str]] = {}
    for path in sorted(DATASETS.glob("*.csv")):
        with open(path, newline="", encoding="utf-8") as handle:
            columns[path.stem] = next(csv.reader(handle))
    return columns


@pytest.fixture(scope="module")
def registry() -> MetricRegistry:
    return load_registry()


def _problems(reg: MetricRegistry, schema: dict[str, list[str]]) -> list[str]:
    return validate_registry(reg, schema, NUMERIC_COLUMNS)


def _with_metric(reg: MetricRegistry, metric_id: str, **changes: Any) -> MetricRegistry:
    metrics = tuple(dataclasses.replace(m, **changes) if m.id == metric_id else m for m in reg.metrics)
    return dataclasses.replace(reg, metrics=metrics)


class TestRealRegistry:
    def test_versions(self, registry: MetricRegistry) -> None:
        assert registry.registry_version == "1.0.0"
        assert registry.schema_version == 1
        assert registry.currency == "unconfirmed"

    def test_validates_clean_against_csv_headers(self, registry, schema_columns) -> None:
        assert _problems(registry, schema_columns) == []

    def test_every_quantitative_column_is_a_base_measure(self, registry) -> None:
        base = {m.source_column for m in registry.metrics if m.source_column}
        assert set(NUMERIC_COLUMNS) <= base

    def test_schema_coverage_both_directions(self, registry, schema_columns) -> None:
        all_columns = {f"{t}.{c}" for t, cols in schema_columns.items() for c in cols}
        base = {m.source_column for m in registry.metrics if m.source_column}
        dims = {d.source for d in registry.dimensions}
        keys = {f"{t}.{k}" for t, k in registry.primary_keys.items()} | {
            c for j in registry.joins for c in (j.from_column, j.to_column)}
        excluded = {e["column"] for e in registry.excluded_columns}
        assert all(e["reason"] for e in registry.excluded_columns)
        assert excluded == {"dim_customers.customer_name"}
        assert all_columns == base | dims | keys | excluded

    def test_numeric_column_list_matches_csv_headers(self, schema_columns) -> None:
        for ref in NUMERIC_COLUMNS:
            table, _, column = ref.partition(".")
            assert column in schema_columns[table]

    def test_money_metrics_have_unconfirmed_currency_and_precision(self, registry) -> None:
        for metric in registry.metrics:
            if metric.unit == "money":
                assert metric.display.get("precision") == 2, metric.id
                assert metric.display.get("rounding") == "ROUND_HALF_UP", metric.id

    def test_all_display_rounding_is_half_up(self, registry) -> None:
        for metric in registry.metrics:
            assert metric.display.get("rounding") == "ROUND_HALF_UP", metric.id

    def test_lookup(self, registry) -> None:
        assert registry.lookup("total_net_sales").unit == "money"
        with pytest.raises(KeyError):
            registry.lookup("no_such_metric")

    def test_joins_and_keys(self, registry) -> None:
        assert {(j.from_column, j.to_column, j.cardinality) for j in registry.joins} == {
            ("fact_sales.customer_id", "dim_customers.customer_id", "many_to_one"),
            ("fact_sales.product_id", "dim_products.product_id", "many_to_one"),
        }
        assert registry.primary_keys == {
            "fact_sales": "sale_id", "dim_products": "product_id", "dim_customers": "customer_id"}


class TestSynonyms:
    def test_average_price_is_ambiguous(self, registry) -> None:
        assert set(registry.resolve_synonym("Average Price")) == {
            "average_listing_price_per_product", "mean_line_unit_price",
            "quantity_weighted_mean_unit_price", "average_realised_net_price_per_unit"}
        assert len(registry.resolve_synonym("Average Price")) == 4

    def test_average_transaction_value_clarifies_to_line_average(self, registry) -> None:
        assert registry.resolve_synonym("average transaction value") == ["average_net_sales_per_line"]
        assert "average transaction value" not in registry.lookup("average_net_sales_per_line").synonyms
        raw = yaml.safe_load((ROOT / "src/semantics/metrics.yaml").read_text(encoding="utf-8"))
        phrase = next(a for a in raw["ambiguous_phrases"] if a["phrase"] == "average transaction value")
        assert phrase["policy"] == "clarify"

    def test_base_labels_do_not_collide_with_total_synonyms(self, registry) -> None:
        assert registry.resolve_synonym("net revenue") == ["total_net_sales"]
        assert registry.resolve_synonym("units sold") == ["total_units"]
        assert registry.lookup("net_revenue").label == "Line net revenue"
        assert registry.lookup("quantity").label == "Line units"

    def test_discounted_unit_price_has_no_mean(self, registry) -> None:
        assert "mean" not in registry.lookup("discounted_unit_price").allowed_operations

    def test_average_category_sales_is_ambiguous(self, registry) -> None:
        assert set(registry.resolve_synonym("average category sales")) == {
            "average_group_total_sold_groups", "average_group_total_full_population"}

    def test_net_sales_is_unique(self, registry) -> None:
        assert registry.resolve_synonym("net sales") == ["total_net_sales"]
        assert registry.resolve_synonym("  NET   sales ") == ["total_net_sales"]

    def test_unknown_phrase_returns_nothing(self, registry) -> None:
        assert registry.resolve_synonym("average order value") == []


class TestValidatorCatchesProblems:
    def test_duplicate_id(self, registry, schema_columns) -> None:
        reg = dataclasses.replace(registry, metrics=registry.metrics + (registry.metrics[0],))
        assert any("duplicate id" in p for p in _problems(reg, schema_columns))

    def test_unknown_operation(self, registry, schema_columns) -> None:
        calc = Calculation(op="median", over="line", operands=(Operand("column", "fact_sales.net_revenue"),))
        reg = _with_metric(registry, "total_net_sales", calculation=calc)
        assert any("unknown operation 'median'" in p for p in _problems(reg, schema_columns))

    def test_unknown_metric_operand(self, registry, schema_columns) -> None:
        calc = Calculation(op="safe_divide", operands=(Operand("metric", "total_ghost"),
                                                       Operand("metric", "total_units")))
        reg = _with_metric(registry, "quantity_weighted_mean_unit_price", calculation=calc)
        assert any("unknown metric 'total_ghost'" in p for p in _problems(reg, schema_columns))

    def test_unknown_column_operand(self, registry, schema_columns) -> None:
        calc = Calculation(op="sum", over="line", operands=(Operand("column", "fact_sales.order_id"),))
        reg = _with_metric(registry, "total_net_sales", calculation=calc)
        assert any("unknown column 'fact_sales.order_id'" in p for p in _problems(reg, schema_columns))

    def test_incompatible_units(self, registry, schema_columns) -> None:
        calc = Calculation(op="add", operands=(Operand("metric", "total_net_sales"),
                                               Operand("metric", "transaction_line_count")))
        reg = _with_metric(registry, "difference", calculation=calc)
        assert any("incompatible units" in p for p in _problems(reg, schema_columns))

    def test_missing_money_precision(self, registry, schema_columns) -> None:
        reg = _with_metric(registry, "total_profit", display={})
        assert any("without display precision" in p for p in _problems(reg, schema_columns))

    def test_mean_of_group_totals_needs_group_grain(self, registry, schema_columns) -> None:
        reg = _with_metric(registry, "average_group_total_sold_groups", grain="dataset")
        assert any("already-aggregated" in p for p in _problems(reg, schema_columns))

    def test_unregistered_numeric_column(self, registry, schema_columns) -> None:
        reg = dataclasses.replace(registry, metrics=tuple(
            m for m in registry.metrics if m.source_column != "fact_sales.tax_amount"))
        assert any("'fact_sales.tax_amount' is not registered" in p for p in _problems(reg, schema_columns))

    def test_source_column_missing_from_schema(self, registry, schema_columns) -> None:
        reg = _with_metric(registry, "tax_amount", source_column="fact_sales.gst_amount")
        assert any("not in schema" in p for p in _problems(reg, schema_columns))

    def test_ambiguous_phrase_unknown_metric(self, registry, schema_columns) -> None:
        reg = dataclasses.replace(registry, ambiguous_phrases={"average price": ("ghost_metric",)})
        assert any("ghost_metric" in p for p in _problems(reg, schema_columns))

    def test_temp_yaml_copy_with_unknown_operation(self, tmp_path, schema_columns) -> None:
        text = (ROOT / "src/semantics/metrics.yaml").read_text(encoding="utf-8")
        broken = tmp_path / "metrics.yaml"
        broken.write_text(text.replace("{op: share,", "{op: ratio_of,", 1), encoding="utf-8")
        problems = _problems(load_registry(broken), schema_columns)
        assert any("unknown operation 'ratio_of'" in p for p in problems)


# ------------------------------------------------- hand-calculated examples

def _example(registry: MetricRegistry, metric: str, filter_text: str) -> Decimal:
    matches = [e for e in registry.examples if e["metric"] == metric and str(e["filter"]) == filter_text]
    assert len(matches) == 1, (metric, filter_text)
    return Decimal(matches[0]["value"])


def _sales(prefix: str = "") -> list[dict[str, str]]:
    return [s for s in _rows("fact_sales") if s["sale_date"].startswith(prefix)]


def _net_by(dimension: str, prefix: str) -> dict[str, Decimal]:
    product_attr = {p["product_id"]: p[dimension] for p in _rows("dim_products")}
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for s in _sales(prefix):
        totals[product_attr[s["product_id"]]] += Decimal(s["net_revenue"])
    return totals


class TestHandCalculatedExamples:
    def test_average_listing_price_per_product(self, registry) -> None:
        products = _rows("dim_products")
        total = sum((Decimal(p["list_price"]) for p in products), Decimal(0))
        assert (total, len(products)) == (Decimal("414.60"), 40)
        assert total / 40 == _example(registry, "average_listing_price_per_product", "none") == Decimal("10.365")

    def test_mean_line_unit_price(self, registry) -> None:
        lines = _sales()
        total = sum((Decimal(s["unit_price"]) for s in lines), Decimal(0))
        assert (total, len(lines)) == (Decimal("12307.53"), 1260)
        assert total / 1260 == _example(registry, "mean_line_unit_price", "none")

    def test_quantity_weighted_mean_unit_price(self, registry) -> None:
        lines = _sales()
        gross = sum((Decimal(s["gross_revenue"]) for s in lines), Decimal(0))
        units = sum(int(s["quantity"]) for s in lines)
        weighted = sum((Decimal(s["unit_price"]) * int(s["quantity"]) for s in lines), Decimal(0))
        assert (gross, units, weighted) == (Decimal("50113.00"), 6148, Decimal("50113.00"))
        assert gross / units == _example(registry, "quantity_weighted_mean_unit_price", "none")

    def test_mean_line_discount_pct(self, registry) -> None:
        lines = _sales()
        total = sum((Decimal(s["discount_pct"]) for s in lines), Decimal(0))
        assert total == Decimal("79.70")
        assert total / 1260 == _example(registry, "mean_line_discount_pct", "none")

    def test_effective_discount_rate(self, registry) -> None:
        lines = _sales()
        discount = sum((Decimal(s["discount_amount"]) for s in lines), Decimal(0))
        gross = sum((Decimal(s["gross_revenue"]) for s in lines), Decimal(0))
        assert discount == Decimal("3095.70")
        assert discount / gross == _example(registry, "effective_discount_rate", "none")
        assert discount / gross != _example(registry, "mean_line_discount_pct", "none")

    def test_average_net_sales_per_line(self, registry) -> None:
        lines = _sales()
        net = sum((Decimal(s["net_revenue"]) for s in lines), Decimal(0))
        assert net == Decimal("47017.30")
        assert net / len(lines) == _example(registry, "average_net_sales_per_line", "none")

    def test_average_category_total_2025_equal_populations(self, registry) -> None:
        totals = _net_by("category", "2025")
        categories = {p["category"] for p in _rows("dim_products")}
        assert len(totals) == len(categories) == 6
        grand = sum(totals.values(), Decimal(0))
        assert grand == Decimal("24800.65")
        assert grand / 6 == _example(registry, "average_group_total_sold_groups", "sale_date in 2025")
        assert grand / 6 == _example(registry, "average_group_total_full_population", "sale_date in 2025")

    def test_average_subcategory_total_jan_2025_populations_differ(self, registry) -> None:
        totals = _net_by("subcategory", "2025-01")
        members = {p["subcategory"] for p in _rows("dim_products")}
        assert (len(totals), len(members)) == (22, 28)
        grand = sum(totals.values(), Decimal(0))
        assert grand == Decimal("1462.66")
        sold = grand / len(totals)
        full = (grand + Decimal(0) * (len(members) - len(totals))) / len(members)
        assert sold == _example(registry, "average_group_total_sold_groups", "sale_date in 2025-01")
        assert full == _example(registry, "average_group_total_full_population", "sale_date in 2025-01")
        assert sold != full

    def test_profit_margin_pct_2025(self, registry) -> None:
        lines = _sales("2025")
        profit = sum((Decimal(s["profit_amount"]) for s in lines), Decimal(0))
        net = sum((Decimal(s["net_revenue"]) for s in lines), Decimal(0))
        assert (profit, net) == (Decimal("13866.10"), Decimal("24800.65"))
        assert profit / net * 100 == _example(registry, "profit_margin_pct", "sale_date in 2025")

    def test_average_realised_net_price_per_unit(self, registry) -> None:
        lines = _sales()
        net = sum((Decimal(s["net_revenue"]) for s in lines), Decimal(0))
        units = sum(int(s["quantity"]) for s in lines)
        assert (net, units) == (Decimal("47017.30"), 6148)
        value = _example(registry, "average_realised_net_price_per_unit", "none")
        assert net / units == value == Decimal("7.647576447625243981782693559")
        assert value != _example(registry, "quantity_weighted_mean_unit_price", "none")


# ------------------------------------------------- negative tests per check

def _replace_calc(reg: MetricRegistry, metric_id: str, **changes: Any) -> MetricRegistry:
    original = reg.lookup(metric_id).calculation
    assert original is not None
    calc = dataclasses.replace(original, **changes)
    return _with_metric(reg, metric_id, calculation=calc)


class TestMoreValidatorChecks:
    def _has(self, reg, schema, text) -> bool:
        return any(text in p for p in _problems(reg, schema))

    def test_unknown_grain(self, registry, schema_columns) -> None:
        assert self._has(_with_metric(registry, "total_profit", grain="galaxy"), schema_columns, "unknown grain")

    def test_unknown_default_aggregation(self, registry, schema_columns) -> None:
        reg = _with_metric(registry, "total_profit", default_aggregation="median")
        assert self._has(reg, schema_columns, "unknown default aggregation")

    @pytest.mark.parametrize("metric_id", ["reward_points", "quantity", "mean_line_discount_pct", "profit_margin_pct"])
    def test_precision_required_for_every_unit(self, registry, schema_columns, metric_id) -> None:
        assert self._has(_with_metric(registry, metric_id, display={}), schema_columns, "without display precision")

    def test_constant_must_be_decimal(self, registry, schema_columns) -> None:
        calc = Calculation(op="subtract", operands=(Operand("constant", "one"),
                                                     Operand("column", "fact_sales.discount_pct")))
        reg = _with_metric(registry, "discounted_unit_price", calculation=calc)
        assert self._has(reg, schema_columns, "not a finite Decimal")

    @pytest.mark.parametrize("changes", [
        {"group_population": "everyone"},
        {"group_population": None},
        {"empty_group_value": None},
    ])
    def test_full_population_group_rules(self, registry, schema_columns, changes) -> None:
        reg = _replace_calc(registry, "average_group_total_full_population", **changes)
        assert self._has(reg, schema_columns, "group_population") or self._has(reg, schema_columns, "empty_group_value")

    def test_sold_groups_must_not_declare_empty_value(self, registry, schema_columns) -> None:
        reg = _replace_calc(registry, "average_group_total_sold_groups", empty_group_value="0")
        assert self._has(reg, schema_columns, "empty_group_value must be present exactly when")

    def test_unknown_allowed_dimension(self, registry, schema_columns) -> None:
        reg = _with_metric(registry, "total_profit", allowed_dimensions=("category", "planet"))
        assert self._has(reg, schema_columns, "'planet' is not a known dimension")

    def test_direct_cycle(self, registry, schema_columns) -> None:
        calc = Calculation(op="identity", operands=(Operand("metric", "total_profit"),))
        reg = _with_metric(registry, "total_profit", calculation=calc)
        assert self._has(reg, schema_columns, "metric 'total_profit': calculation references itself")

    def test_transitive_cycle(self, registry, schema_columns) -> None:
        def ref(target: str) -> Calculation:
            return Calculation(op="identity", operands=(Operand("metric", target),))
        reg = _with_metric(_with_metric(registry, "total_profit", calculation=ref("total_units")),
                           "total_units", calculation=ref("total_profit"))
        assert self._has(reg, schema_columns, "metric 'total_units': calculation references itself")

    def test_declared_unit_must_match_inferred(self, registry, schema_columns) -> None:
        reg = _with_metric(registry, "effective_discount_rate", unit="percent")
        assert self._has(reg, schema_columns, "calculation implies 'fraction'")
        reg = _with_metric(registry, "profit_margin_pct", unit="fraction")
        assert self._has(reg, schema_columns, "calculation implies 'percent'")

    def test_label_collision_with_synonym(self, registry, schema_columns) -> None:
        reg = _with_metric(registry, "net_revenue", label="Net revenue")
        assert self._has(reg, schema_columns, "name 'net revenue'")

    def test_base_measure_on_key_column(self, registry, schema_columns) -> None:
        reg = _with_metric(registry, "quantity", source_column="fact_sales.sale_id")
        assert self._has(reg, schema_columns, "base measure on key column")

    def test_dimension_source_not_in_schema(self, registry, schema_columns) -> None:
        dims = (dataclasses.replace(registry.dimensions[0], source="dim_products.ghost"),) + registry.dimensions[1:]
        reg = dataclasses.replace(registry, dimensions=dims)
        assert self._has(reg, schema_columns, "dimension 'product_id': source column 'dim_products.ghost'")

    def test_join_column_not_in_schema(self, registry, schema_columns) -> None:
        join = JoinDefinition("bad_join", "fact_sales.ghost", "dim_products.product_id", "many_to_one")
        reg = dataclasses.replace(registry, joins=registry.joins + (join,))
        assert self._has(reg, schema_columns, "join 'bad_join': column 'fact_sales.ghost'")

    def test_example_unknown_metric(self, registry, schema_columns) -> None:
        reg = dataclasses.replace(registry, examples=registry.examples + ({"metric": "ghost"},))
        assert self._has(reg, schema_columns, "example names unknown metric 'ghost'")

    @pytest.mark.parametrize("changes", [
        {"source_column": "fact_sales.net_revenue"},  # both
        {"calculation": None},  # neither (total_net_sales has only a calculation)
    ])
    def test_exactly_one_of_source_or_calculation(self, registry, schema_columns, changes) -> None:
        reg = _with_metric(registry, "total_net_sales", **changes)
        assert self._has(reg, schema_columns, "exactly one of source column or calculation")

    def test_unknown_allowed_operation(self, registry, schema_columns) -> None:
        reg = _with_metric(registry, "total_profit", allowed_operations=("identity", "median"))
        assert self._has(reg, schema_columns, "unknown allowed operation 'median'")

    def test_unknown_unit(self, registry, schema_columns) -> None:
        assert self._has(_with_metric(registry, "total_profit", unit="furlongs"), schema_columns, "unknown unit")
