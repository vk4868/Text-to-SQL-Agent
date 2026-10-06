"""Typed query intent, a bounded registry-driven SQL compiler and the result binder (Phase 3).

Trust boundaries:

* The model proposes a ``QueryIntent`` as JSON. ``parse_query_intent`` accepts it
  only when every field is known and every metric/dimension resolves in the
  registry. An ambiguous phrase yields ``ClarificationRequired``; the engine never
  picks one candidate silently.
* ``compile_query`` renders SQL from the registry calculation tree for a small,
  explicit set of shapes. Anything else is a ``CompileRejection`` and never falls
  back to model-written SQL. The compiled SQL still has to go through
  ``SQLExecutionPipeline.execute()``; nothing here talks to BigQuery.
* Output-column-to-metric bindings are established by the compiler from the plan,
  never from aliases in model text. ``bind_result`` turns a pipeline result into
  an engine-owned ``ResultManifest`` whose completeness the model cannot assert.

Nothing is read at import time: the registry and ``src.config`` are consulted
when the functions are called.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from src.semantics.registry import Calculation, MetricDefinition, MetricRegistry, Operand

FILTER_OPS = frozenset({"eq", "in", "between"})
DATE_GRAINS = frozenset({"month", "year"})
ORDER_DIRECTIONS = frozenset({"asc", "desc"})
MAX_FILTER_VALUES = 50
MAX_FILTER_VALUE_LENGTH = 200
MAX_CALC_DEPTH = 6

FACT_TABLE = "fact_sales"
TABLE_ALIASES = {"fact_sales": "f", "dim_products": "p", "dim_customers": "c"}
DATE_GRAIN_COLUMNS = {"month": "sale_month", "year": "sale_year"}

_INTENT_FIELDS = frozenset({"metric_id", "dimensions", "filters", "date_grain", "order", "limit", "request_id"})
_CLARIFY_FIELDS = frozenset({"ambiguous_phrase", "request_id"})
_FILTER_FIELDS = frozenset({"dimension_id", "op", "values"})
_ORDER_FIELDS = frozenset({"field", "direction"})
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_DECIMAL_LITERAL = re.compile(r"-?[0-9]+(\.[0-9]+)?")

# Golden-case coverage of this compiler slice (evaluation/golden_cases.yaml).
# "supported" means the intent below compiles to an equivalent aggregate shape;
# live result equivalence is reserved for Phase 7.
QUESTION_SHAPE_COVERAGE: dict[str, dict[str, str]] = {
    "net_sales_by_month_2025": {"status": "supported", "shape": "month grain",
                                "intent": "total_net_sales, date_grain=month, sale_date between 2025"},
    "total_net_sales_2025": {"status": "supported", "shape": "scalar",
                             "intent": "total_net_sales, sale_date between 2025"},
    "total_profit_2025": {"status": "supported", "shape": "scalar",
                          "intent": "total_profit, sale_date between 2025"},
    "profit_by_category_2025": {"status": "supported", "shape": "grouped top-N via dim_products join",
                                "intent": "total_profit by category, order desc, limit 1"},
    "top_5_products_by_revenue_2025": {"status": "supported", "shape": "grouped top-N via dim_products join",
                                       "intent": "total_net_sales by product_name, order desc, limit 5"},
    "revenue_by_region_2025": {"status": "supported", "shape": "grouped by fact column",
                               "intent": "total_net_sales by branch_region"},
    "electronics_november_december_2025": {"status": "supported", "shape": "scalar with product filter (join)",
                                           "intent": "total_net_sales, category eq Electronics, "
                                                     "sale_date between 2025-11-01..2025-12-31"},
    "sales_by_membership_status_2025": {"status": "supported", "shape": "grouped via dim_customers join",
                                        "intent": "total_net_sales by membership_status"},
    "transaction_count_2025": {"status": "supported", "shape": "scalar count",
                               "intent": "transaction_line_count (COUNT(sale_id), not COUNT(*))"},
    "average_discount_by_promotion_2025": {"status": "clarification_required", "shape": "grouped mean",
                                           "intent": "mean_line_discount_pct by promotion_type; 'average discount' "
                                                     "is an ambiguous phrase, so the user must choose this "
                                                     "candidate (vs effective_discount_rate) first"},
    "profit_margin_by_category_2025": {"status": "supported", "shape": "grouped ratio-of-sums via join",
                                       "intent": "profit_margin_pct by category"},
    "sales_by_channel_2025": {"status": "supported", "shape": "grouped by fact column",
                              "intent": "total_net_sales by sales_channel; the 'split' (share_of_total) is a "
                                        "claim-level calculation for Phase 4, not compiled SQL"},
    "quantity_sold_by_category_2025": {"status": "supported", "shape": "grouped via dim_products join",
                                       "intent": "total_units by category"},
    "best_month_for_profit_2025": {"status": "supported", "shape": "month grain top-N",
                                   "intent": "total_profit, date_grain=month, order desc, limit 1"},
    "no_sales_in_1999": {"status": "supported", "shape": "month grain (empty result)",
                         "intent": "total_net_sales, date_grain=month, sale_date between 1999"},
}
UNSUPPORTED_SHAPES: dict[str, str] = {
    "average_group_total_*": "mean of group totals needs a two-level aggregation plan; not in this slice",
    "share_of_total / percentage_change / difference": "parameterised claim-level operations, not query metrics",
    "base line measures (e.g. quantity, unit_price)": "line-grain values need an aggregation; use the dataset metric",
    "product-grain metrics (average_listing_price_per_product)": "aggregates over dim_products, not fact_sales",
    "multiple grouping dimensions": "only one grouping key (one dimension or one date grain) is compiled",
    "weighted_mean / min / max / share ops": "no renderer in this slice",
}


# ---------------------------------------------------------------- intent

@dataclass(frozen=True)
class Filter:
    dimension_id: str
    op: str  # eq | in | between
    values: tuple[str, ...]


@dataclass(frozen=True)
class QueryIntent:
    metric_id: str
    dimensions: tuple[str, ...]
    filters: tuple[Filter, ...]
    date_grain: Literal["month", "year"] | None
    order: tuple[tuple[str, str], ...]
    limit: int | None
    request_id: str


@dataclass(frozen=True)
class IntentRejection:
    reason_code: str
    message: str


@dataclass(frozen=True)
class ClarificationRequired:
    phrase: str
    candidates: tuple[str, ...]
    request_id: str = ""


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _reject(code: str, message: str) -> IntentRejection:
    return IntentRejection(code, message)


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def parse_query_intent(text: str, registry: MetricRegistry, *, question: str = "",
                       resolved_ambiguities: tuple[str, ...] = (),
                       expected_request_id: str | None = None,
                       ) -> QueryIntent | IntentRejection | ClarificationRequired:
    """Strictly parse model JSON into a QueryIntent; never guesses or repairs.

    When ``question`` is given, a registered ambiguous phrase in it whose candidates include the
    chosen metric yields ``ClarificationRequired`` unless the phrase is in ``resolved_ambiguities``.
    """
    from src import config  # call time: caps stay overridable

    try:
        raw = json.loads(text)
    except (TypeError, ValueError) as exc:
        return _reject("invalid_json", f"intent is not valid JSON: {exc}")
    if not isinstance(raw, dict):
        return _reject("not_an_object", "intent must be a JSON object")
    keys = set(raw)

    if "ambiguous_phrase" in keys:
        if keys != _CLARIFY_FIELDS:
            return _reject("unknown_fields", f"clarification must have exactly {sorted(_CLARIFY_FIELDS)}")
        phrase, request_id = raw["ambiguous_phrase"], raw["request_id"]
        if not isinstance(phrase, str) or not isinstance(request_id, str):
            return _reject("invalid_field_type", "ambiguous_phrase and request_id must be strings")
        candidates = registry.ambiguous_phrases.get(_norm(phrase))
        if not candidates:
            return _reject("unknown_ambiguous_phrase", f"{phrase!r} is not a registered ambiguous phrase")
        return ClarificationRequired(_norm(phrase), tuple(candidates), request_id)

    if keys - _INTENT_FIELDS:
        return _reject("unknown_fields", f"unknown intent fields: {sorted(keys - _INTENT_FIELDS)}")
    if _INTENT_FIELDS - keys:
        return _reject("missing_fields", f"missing intent fields: {sorted(_INTENT_FIELDS - keys)}")

    metric_id, request_id = raw["metric_id"], raw["request_id"]
    if not isinstance(metric_id, str) or not isinstance(request_id, str) or not request_id:
        return _reject("invalid_field_type", "metric_id and request_id must be non-empty strings")
    metrics = {m.id: m for m in registry.metrics}
    if metric_id not in metrics:
        return _reject("unknown_metric", f"unknown metric id {metric_id!r}")
    metric = metrics[metric_id]
    dimension_ids = {d.id for d in registry.dimensions}

    if not _string_list(raw["dimensions"]):
        return _reject("invalid_field_type", "dimensions must be a list of strings")
    dimensions = tuple(raw["dimensions"])
    if len(set(dimensions)) != len(dimensions):
        return _reject("invalid_field_type", "dimensions must not repeat")

    if not isinstance(raw["filters"], list):
        return _reject("invalid_field_type", "filters must be a list")
    filters: list[Filter] = []
    for item in raw["filters"]:
        if not isinstance(item, dict) or set(item) != _FILTER_FIELDS:
            return _reject("invalid_filter", f"each filter must have exactly {sorted(_FILTER_FIELDS)}")
        if not isinstance(item["dimension_id"], str) or item["op"] not in FILTER_OPS:
            return _reject("invalid_filter", f"filter op must be one of {sorted(FILTER_OPS)}")
        if not isinstance(item["values"], list) or not item["values"]:
            return _reject("invalid_filter", "filter values must be a non-empty list")
        if not all(isinstance(v, str) for v in item["values"]):
            return _reject("non_string_filter_value", "filter values must all be strings")
        filters.append(Filter(item["dimension_id"], item["op"], tuple(item["values"])))

    for dim in (*dimensions, *(f.dimension_id for f in filters)):
        if dim not in dimension_ids:
            return _reject("unknown_dimension", f"unknown dimension {dim!r}")
        if dim not in metric.allowed_dimensions:
            return _reject("dimension_not_allowed", f"dimension {dim!r} is not allowed for metric {metric_id!r}")

    date_grain = raw["date_grain"]
    if date_grain is not None and date_grain not in DATE_GRAINS:
        return _reject("invalid_date_grain", f"date_grain must be null or one of {sorted(DATE_GRAINS)}")
    if date_grain is not None and "sale_date" not in metric.allowed_dimensions:
        return _reject("dimension_not_allowed", f"sale_date is not allowed for metric {metric_id!r}")

    if not isinstance(raw["order"], list):
        return _reject("invalid_order", "order must be a list")
    order: list[tuple[str, str]] = []
    for item in raw["order"]:
        if (not isinstance(item, dict) or set(item) != _ORDER_FIELDS or not isinstance(item["field"], str)
                or item["direction"] not in ORDER_DIRECTIONS):
            return _reject("invalid_order", "each order item must be {field: str, direction: asc|desc}")
        order.append((item["field"], item["direction"]))

    limit = raw["limit"]
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int):
            return _reject("invalid_field_type", "limit must be an integer or null")
        if limit < 1 or limit > config.MAX_RESULT_ROWS:
            return _reject("limit_out_of_range", f"limit must be between 1 and {config.MAX_RESULT_ROWS}")

    if expected_request_id is not None and request_id != expected_request_id:
        return _reject("request_id_mismatch", "request_id does not match the engine-issued value")

    if question:
        normalised_question = _norm(question)
        resolved = {_norm(p) for p in resolved_ambiguities}
        for phrase, candidates in registry.ambiguous_phrases.items():
            key = _norm(phrase)
            if key in normalised_question and metric_id in candidates and key not in resolved:
                return ClarificationRequired(key, tuple(candidates), request_id)

    return QueryIntent(metric_id, dimensions, tuple(filters), date_grain, tuple(order), limit, request_id)


# ---------------------------------------------------------------- compiler

@dataclass(frozen=True)
class MetricBinding:
    binding_id: str  # output column name
    kind: Literal["metric", "dimension"]
    ref_id: str  # metric id or dimension id
    unit: str | None
    currency: str | None
    grain: str | None


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    plan_id: str
    bindings: tuple[MetricBinding, ...]
    grouping_grain: str
    filters: tuple[Filter, ...]
    population: str
    completeness_hint: Literal["complete", "unknown"]
    limit: int | None
    registry_version: str


@dataclass(frozen=True)
class CompileRejection:
    reason_code: str
    message: str


class _Unsupported(Exception):
    pass


def _plan_id(intent: QueryIntent, registry_version: str) -> str:
    canonical = json.dumps({
        "registry_version": registry_version,
        "metric_id": intent.metric_id,
        "dimensions": list(intent.dimensions),
        "filters": [[f.dimension_id, f.op, list(f.values)] for f in intent.filters],
        "date_grain": intent.date_grain,
        "order": [list(o) for o in intent.order],
        "limit": intent.limit,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sql_string_literal(value: str) -> str:
    """Quote a filter value as a BigQuery string literal; control characters are refused."""
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise _Unsupported("filter value contains a control character")
    if len(value) > MAX_FILTER_VALUE_LENGTH:
        raise _Unsupported("filter value is too long")
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _date_literal(value: str) -> str:
    if not _ISO_DATE.fullmatch(value):
        raise _Unsupported(f"date filter value {value!r} is not YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise _Unsupported(f"invalid date {value!r}") from exc
    return f"DATE('{value}')"


class _Renderer:
    """Render a registry calculation tree into a SQL aggregate expression over fact_sales."""

    def __init__(self, metrics: dict[str, MetricDefinition]) -> None:
        self.metrics = metrics

    def metric(self, metric: MetricDefinition, depth: int = 0) -> str:
        if metric.calculation is None or metric.grain != "dataset":
            raise _Unsupported(f"metric {metric.id!r} (grain {metric.grain!r}) is not a dataset aggregate")
        return self.calc(metric.calculation, depth)

    def calc(self, calc: Calculation, depth: int) -> str:
        if depth > MAX_CALC_DEPTH:
            raise _Unsupported("calculation exceeds the depth bound")
        if calc.over == "group":
            raise _Unsupported("group-level aggregation is not supported in this slice")
        op, operands = calc.op, calc.operands
        if op in {"sum", "count", "distinct_count", "mean"}:
            if calc.over != "line" or len(operands) != 1 or operands[0].kind != "column":
                raise _Unsupported(f"{op} must aggregate one fact_sales column over line")
            column = self.column(operands[0])
            return {"sum": f"SUM({column})", "count": f"COUNT({column})",
                    "distinct_count": f"COUNT(DISTINCT {column})", "mean": f"AVG({column})"}[op]
        if op == "safe_divide" and len(operands) == 2:
            return f"SAFE_DIVIDE({self.operand(operands[0], depth)}, {self.operand(operands[1], depth)})"
        if op in {"multiply", "add", "subtract"} and len(operands) == 2:
            symbol = {"multiply": "*", "add": "+", "subtract": "-"}[op]
            return f"({self.operand(operands[0], depth)} {symbol} {self.operand(operands[1], depth)})"
        raise _Unsupported(f"operation {op!r} has no renderer in this slice")

    def operand(self, operand: Operand, depth: int) -> str:
        if operand.kind == "metric":
            if operand.ref not in self.metrics:
                raise _Unsupported(f"unknown metric operand {operand.ref!r}")
            return self.metric(self.metrics[operand.ref], depth + 1)
        if operand.kind == "constant":
            if not _DECIMAL_LITERAL.fullmatch(operand.ref):
                raise _Unsupported(f"constant {operand.ref!r} is not a plain decimal")
            try:
                Decimal(operand.ref)
            except InvalidOperation as exc:  # pragma: no cover - regex already guards
                raise _Unsupported(f"bad constant {operand.ref!r}") from exc
            return operand.ref
        if operand.kind == "calculation" and operand.calculation is not None:
            return self.calc(operand.calculation, depth + 1)
        # A bare column outside an aggregate, or a template parameter, is not compiled.
        raise _Unsupported(f"operand kind {operand.kind!r} is not allowed here")

    @staticmethod
    def column(operand: Operand) -> str:
        table, _, column = operand.ref.partition(".")
        if table != FACT_TABLE or not column.isidentifier():
            raise _Unsupported(f"aggregated column {operand.ref!r} is not a fact_sales column")
        return f"f.{column}"


def compile_query(intent: QueryIntent, registry: MetricRegistry, *, project_id: str,
                  dataset_id: str) -> CompiledQuery | CompileRejection:
    """Compile a validated intent into one read-only BigQuery statement, or reject it."""
    try:
        return _compile(intent, registry, project_id, dataset_id)
    except _Unsupported as exc:
        return CompileRejection("unsupported_shape", str(exc))


def _compile(intent: QueryIntent, registry: MetricRegistry, project_id: str, dataset_id: str) -> CompiledQuery:
    from src import config

    metrics = {m.id: m for m in registry.metrics}
    dims = {d.id: d for d in registry.dimensions}
    if intent.metric_id not in metrics:
        raise _Unsupported(f"unknown metric {intent.metric_id!r}")
    metric = metrics[intent.metric_id]
    for ident in (project_id, dataset_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", ident):
            raise _Unsupported("project/dataset id contains unexpected characters")
    for dim_id in (*intent.dimensions, *(f.dimension_id for f in intent.filters)):
        if dim_id not in dims or dim_id not in metric.allowed_dimensions:
            raise _Unsupported(f"dimension {dim_id!r} is unknown or not allowed for {metric.id!r}")
    if len(intent.dimensions) + (intent.date_grain is not None) > 1:
        raise _Unsupported("only one grouping key (one dimension or one date grain) is supported")
    if intent.limit is not None and (intent.limit < 1 or intent.limit > config.MAX_RESULT_ROWS):
        raise _Unsupported("limit out of range")

    measure = _Renderer(metrics).metric(metric)
    tables: set[str] = {FACT_TABLE}

    def qualified(source: str) -> str:
        table, _, column = source.partition(".")
        if table not in TABLE_ALIASES or not column.isidentifier():
            raise _Unsupported(f"dimension source {source!r} is not compilable")
        tables.add(table)
        return f"{TABLE_ALIASES[table]}.{column}"

    select: list[str] = []
    group_by: list[str] = []
    bindings: list[MetricBinding] = []
    grouping_grain = "dataset"
    if intent.date_grain is not None:
        sale_date = qualified(dims["sale_date"].source)
        expr = (f"FORMAT_DATE('%Y-%m', {sale_date})" if intent.date_grain == "month"
                else f"EXTRACT(YEAR FROM {sale_date})")
        name = DATE_GRAIN_COLUMNS[intent.date_grain]
        select.append(f"{expr} AS {name}")
        group_by.append(name)
        bindings.append(MetricBinding(name, "dimension", "sale_date", None, None, intent.date_grain))
        grouping_grain = f"sale_date:{intent.date_grain}"
    for dim_id in intent.dimensions:
        dim = dims[dim_id]
        if dim.kind == "date":
            raise _Unsupported("group by a raw date dimension; use date_grain instead")
        select.append(f"{qualified(dim.source)} AS {dim_id}")
        group_by.append(dim_id)
        bindings.append(MetricBinding(dim_id, "dimension", dim_id, None, None, None))
        grouping_grain = dim_id

    currency = metric.raw.get("currency") or (registry.currency if metric.unit == "money" else None)
    select.append(f"{measure} AS {metric.id}")
    bindings.append(MetricBinding(metric.id, "metric", metric.id, metric.unit,
                                  None if currency is None else str(currency), grouping_grain))

    where: list[str] = []
    for flt in intent.filters:
        dim = dims[flt.dimension_id]
        column = qualified(dim.source)
        if len(flt.values) > MAX_FILTER_VALUES:
            raise _Unsupported("too many filter values")
        literal = _date_literal if dim.kind == "date" else sql_string_literal
        if flt.op == "between":
            if dim.kind != "date" or len(flt.values) != 2:
                raise _Unsupported("between needs a date dimension and exactly two values")
            low, high = flt.values
            if low > high:
                raise _Unsupported("between range is reversed")
            where.append(f"{column} BETWEEN {literal(low)} AND {literal(high)}")
        elif flt.op == "eq":
            if len(flt.values) != 1:
                raise _Unsupported("eq needs exactly one value")
            where.append(f"{column} = {literal(flt.values[0])}")
        else:
            where.append(f"{column} IN ({', '.join(literal(v) for v in flt.values)})")

    output_names = {b.binding_id for b in bindings}
    order_sql: list[str] = []
    for field_name, direction in intent.order:
        if field_name not in output_names:
            raise _Unsupported(f"order field {field_name!r} is not an output column of this plan")
        order_sql.append(f"{field_name} {direction.upper()}")
    if not group_by and (intent.order or intent.limit is not None):
        raise _Unsupported("order/limit need a grouping key")

    def table_ref(table: str) -> str:
        return f"`{project_id}.{dataset_id}.{table}` AS {TABLE_ALIASES[table]}"

    joins: list[str] = []
    for table in sorted(tables - {FACT_TABLE}):
        join = next((j for j in registry.joins if j.from_column.startswith(f"{FACT_TABLE}.")
                     and j.to_column.startswith(f"{table}.")), None)
        if join is None or join.cardinality != "many_to_one":
            raise _Unsupported(f"no many-to-one registry join from {FACT_TABLE} to {table}")
        left = f"f.{join.from_column.partition('.')[2]}"
        right = f"{TABLE_ALIASES[table]}.{join.to_column.partition('.')[2]}"
        joins.append(f"JOIN {table_ref(table)} ON {left} = {right}")

    lines = [f"SELECT {', '.join(select)}", f"FROM {table_ref(FACT_TABLE)}", *joins]
    if where:
        lines.append("WHERE " + " AND ".join(where))
    if group_by:
        lines.append("GROUP BY " + ", ".join(group_by))
    if order_sql:
        lines.append("ORDER BY " + ", ".join(order_sql))
    if intent.limit is not None:
        lines.append(f"LIMIT {intent.limit}")

    population = "fact_sales lines" + (
        " where " + "; ".join(f"{f.dimension_id} {f.op} {list(f.values)}" for f in intent.filters)
        if intent.filters else "")
    return CompiledQuery(
        sql="\n".join(lines),
        plan_id=_plan_id(intent, registry.registry_version),
        bindings=tuple(bindings),
        grouping_grain=grouping_grain,
        filters=intent.filters,
        population=population,
        completeness_hint="complete" if not group_by else "unknown",
        limit=intent.limit,
        registry_version=registry.registry_version,
    )


# ---------------------------------------------------------------- result binder

class ResultBindingError(ValueError):
    """The execution result cannot be bound to the compiled plan."""


@dataclass(frozen=True)
class ResultManifest:
    result_id: str
    request_id: str
    question: str
    registry_version: str
    plan_id: str
    execution_id: str | None
    job_id: str | None
    output_schema: tuple[str, ...]
    bindings: tuple[MetricBinding, ...]
    grouping_grain: str
    filters: tuple[Filter, ...]
    population: str
    row_refs: dict[str, dict[str, Any]] = field(default_factory=dict)
    row_count: int = 0
    completeness: Literal["complete", "subset", "unknown"] = "unknown"
    completeness_basis: str = ""
    executed_sql_sha256: str = ""

    @property
    def metric_ids(self) -> tuple[str, ...]:
        return tuple(b.ref_id for b in self.bindings if b.kind == "metric")


def bind_result(compiled: CompiledQuery, execution_result: dict[str, Any], *, request_id: str, question: str,
                registry_version: str, max_result_rows: int | None = None) -> ResultManifest:
    """Bind a successful pipeline result to the compiled plan. Nothing here comes from the model."""
    from src import config

    if execution_result.get("status", "success") != "success":
        raise ResultBindingError("only a successful execution result can be bound")
    if registry_version != compiled.registry_version:
        raise ResultBindingError("registry version differs from the one the plan was compiled with")
    cap = config.MAX_RESULT_ROWS if max_result_rows is None else max_result_rows
    reported_limit = execution_result.get("result_row_limit")
    if isinstance(reported_limit, int) and not isinstance(reported_limit, bool) and reported_limit > 0:
        cap = reported_limit
    executed_sql = execution_result.get("executed_sql")
    if not isinstance(executed_sql, str) or not _executed_sql_matches(compiled.sql, executed_sql, cap):
        raise ResultBindingError("executed_sql does not match the compiled plan")
    rows = list(execution_result.get("rows") or [])
    expected = tuple(b.binding_id for b in compiled.bindings)
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(expected):
            raise ResultBindingError(f"result columns do not match the plan bindings {list(expected)}")
    row_count = int(execution_result.get("row_count", len(rows)))
    if row_count != len(rows):
        raise ResultBindingError("row_count does not match the rows returned")
    total = execution_result.get("total_result_rows")
    truncated = bool(execution_result.get("result_truncated_by_client"))

    if truncated:
        completeness, basis = "subset", "result_truncated_by_client"
    elif isinstance(total, int) and total > row_count:
        completeness, basis = "subset", "total_result_rows > row_count"
    elif execution_result.get("limit_was_modified") is True and row_count == cap:
        completeness, basis = "unknown", "row cap reached"
    elif compiled.grouping_grain == "dataset":
        completeness, basis = ("complete", "scalar_aggregate") if row_count == 1 else ("unknown", "scalar_row_count")
    elif compiled.limit is not None:
        completeness, basis = "unknown", "query_limit_applied"
    elif isinstance(total, int) and total == row_count and row_count < cap:
        completeness, basis = "complete", "total_result_rows == row_count < max_result_rows"
    else:
        completeness, basis = "unknown", "group_count_not_provably_below_cap"

    return ResultManifest(
        result_id=f"result-{uuid.uuid4()}",
        request_id=request_id,
        question=question,
        registry_version=registry_version,
        plan_id=compiled.plan_id,
        execution_id=_opt_str(execution_result.get("run_id") or execution_result.get("job_id")),
        job_id=_opt_str(execution_result.get("job_id")),
        output_schema=expected,
        bindings=compiled.bindings,
        grouping_grain=compiled.grouping_grain,
        filters=compiled.filters,
        population=compiled.population,
        row_refs={f"r{i}": dict(row) for i, row in enumerate(rows)},
        row_count=row_count,
        completeness=completeness,
        completeness_basis=basis,
        executed_sql_sha256=hashlib.sha256(executed_sql.encode("utf-8")).hexdigest(),
    )


def _executed_sql_matches(compiled_sql: str, executed_sql: str, cap: int) -> bool:
    import sqlglot

    from src.sql_validator import enforce_result_limit

    if executed_sql == compiled_sql:
        return True
    allowed = [compiled_sql]
    limited = enforce_result_limit(compiled_sql, max_rows=cap)
    if limited.is_valid and limited.limited_sql:
        allowed.append(limited.limited_sql)
    if executed_sql in allowed:
        return True
    try:
        normal = sqlglot.parse_one(executed_sql, read="bigquery").sql(dialect="bigquery")
        return any(normal == sqlglot.parse_one(a, read="bigquery").sql(dialect="bigquery") for a in allowed)
    except sqlglot.errors.SqlglotError:
        return False


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)
