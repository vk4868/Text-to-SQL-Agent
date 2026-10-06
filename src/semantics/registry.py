"""Typed loader and validator for the semantic metric registry (``metrics.yaml``).

The registry owns metric definitions, units, grain and aggregation rules. It
contains no SQL and no executable expressions: every derived metric is a
``Calculation`` built from a closed set of operations. Nothing here reads a
file at import time; ``load_registry`` reads the YAML when called.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATH = Path(__file__).with_name("metrics.yaml")

OPERATIONS: frozenset[str] = frozenset({
    "identity", "add", "subtract", "multiply", "safe_divide", "sum", "count",
    "distinct_count", "mean", "weighted_mean", "min", "max", "share", "percentage_change",
})
AGGREGATING_OPS = frozenset({"sum", "count", "distinct_count", "mean", "weighted_mean", "min", "max"})
SAME_UNIT_OPS = frozenset({"add", "subtract", "mean", "min", "max", "sum", "weighted_mean"})
UNITS = frozenset({"money", "fraction", "percent", "count", "points", "none"})
AGGREGATE_GRAINS = frozenset({"dataset", "group"})
GRAINS = frozenset({"product", "line", "group", "dataset", "customer"})
GROUP_POPULATIONS = frozenset({"sold_groups", "full_dimension_members"})
TABLE_GRAINS = {"fact_sales": "line", "dim_products": "product", "dim_customers": "customer"}


@dataclass(frozen=True)
class Operand:
    kind: str  # metric | column | constant | parameter | calculation
    ref: str = ""
    calculation: Calculation | None = None


@dataclass(frozen=True)
class Calculation:
    op: str
    operands: tuple[Operand, ...]
    over: str | None = None
    group_population: str | None = None
    empty_group_value: str | None = None


@dataclass(frozen=True)
class MetricDefinition:
    id: str
    version: int
    label: str
    synonyms: tuple[str, ...]
    description: str
    unit: str
    grain: str
    data_type: str
    source_column: str | None = None
    calculation: Calculation | None = None
    allowed_dimensions: tuple[str, ...] = ()
    allowed_operations: tuple[str, ...] = ()
    default_aggregation: str | None = None
    numerator: str | None = None
    denominator: str | None = None
    weight: str | None = None
    null_policy: str = ""
    zero_policy: str = ""
    sign_policy: str = ""
    display: dict[str, Any] = field(default_factory=dict)
    provenance: str = ""
    unsupported_uses: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


@dataclass(frozen=True)
class DimensionDefinition:
    id: str
    label: str
    synonyms: tuple[str, ...]
    source: str
    kind: str  # id | classification | date
    grains: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class JoinDefinition:
    id: str
    from_column: str
    to_column: str
    cardinality: str


@dataclass(frozen=True)
class MetricRegistry:
    registry_version: str
    schema_version: int
    currency: str
    metrics: tuple[MetricDefinition, ...]
    dimensions: tuple[DimensionDefinition, ...]
    joins: tuple[JoinDefinition, ...]
    primary_keys: dict[str, str]
    ambiguous_phrases: dict[str, tuple[str, ...]]
    examples: tuple[dict[str, Any], ...] = ()
    excluded_columns: tuple[dict[str, Any], ...] = ()

    def lookup(self, metric_id: str) -> MetricDefinition:
        for metric in self.metrics:
            if metric.id == metric_id:
                return metric
        raise KeyError(f"unknown metric id: {metric_id!r}")

    def resolve_synonym(self, text: str) -> list[str]:
        """Exact, case-insensitive match. Several candidates are returned, never one picked."""
        key = _norm(text)
        if key in self.ambiguous_phrases:
            return list(self.ambiguous_phrases[key])
        return [m.id for m in self.metrics if key in {_norm(s) for s in (m.id, m.label, *m.synonyms)}]


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _parse_operand(raw: dict[str, Any]) -> Operand:
    if "op" in raw:
        return Operand("calculation", calculation=_parse_calc(raw))
    for kind in ("metric", "column", "constant", "parameter"):
        if kind in raw:
            return Operand(kind, str(raw[kind]))
    raise ValueError(f"unrecognised operand: {raw!r}")


def _parse_calc(raw: dict[str, Any]) -> Calculation:
    empty = raw.get("empty_group_value")
    return Calculation(
        op=str(raw["op"]),
        operands=tuple(_parse_operand(o) for o in raw.get("operands", [])),
        over=raw.get("over"),
        group_population=raw.get("group_population"),
        empty_group_value=None if empty is None else str(empty),
    )


def _parse_metric(raw: dict[str, Any]) -> MetricDefinition:
    source = raw.get("source") or {}
    calc = source.get("calculation")
    return MetricDefinition(
        id=str(raw["id"]), version=int(raw["version"]), label=str(raw["label"]),
        synonyms=tuple(raw.get("synonyms") or ()), description=str(raw.get("description", "")),
        unit=str(raw["unit"]), grain=str(raw["grain"]), data_type=str(raw.get("data_type", "")),
        source_column=source.get("column"), calculation=_parse_calc(calc) if calc else None,
        allowed_dimensions=tuple(raw.get("allowed_dimensions") or ()),
        allowed_operations=tuple(raw.get("allowed_operations") or ()),
        default_aggregation=raw.get("default_aggregation"),
        numerator=raw.get("numerator"), denominator=raw.get("denominator"), weight=raw.get("weight"),
        null_policy=str(raw.get("null_policy", "")), zero_policy=str(raw.get("zero_policy", "")),
        sign_policy=str(raw.get("sign_policy", "")), display=dict(raw.get("display") or {}),
        provenance=str(raw.get("provenance", "")),
        unsupported_uses=tuple(raw.get("unsupported_uses") or ()), raw=raw,
    )


def load_registry(path: str | Path | None = None) -> MetricRegistry:
    """Read and parse the registry YAML. Raises ValueError on a structurally broken file."""
    with open(path or DEFAULT_PATH, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError("metric registry must be a mapping")
    try:
        return MetricRegistry(
            registry_version=str(data["registry_version"]),
            schema_version=int(data["schema_version"]),
            currency=str(data.get("currency", "unconfirmed")),
            metrics=tuple(_parse_metric(m) for m in data.get("metrics") or ()),
            dimensions=tuple(
                DimensionDefinition(str(d["id"]), str(d["label"]), tuple(d.get("synonyms") or ()),
                                    str(d["source"]), str(d["kind"]), tuple(d.get("grains") or ()),
                                    str(d.get("notes", "")))
                for d in data.get("dimensions") or ()),
            joins=tuple(JoinDefinition(str(j["id"]), str(j["from"]), str(j["to"]), str(j["cardinality"]))
                        for j in data.get("joins") or ()),
            primary_keys={t: str(v["primary_key"]) for t, v in (data.get("tables") or {}).items()},
            ambiguous_phrases={_norm(a["phrase"]): tuple(a["candidates"])
                               for a in data.get("ambiguous_phrases") or ()},
            examples=tuple(data.get("examples") or ()),
            excluded_columns=tuple(data.get("excluded_columns") or ()),
        )
    except (KeyError, TypeError) as exc:
        raise ValueError(f"malformed metric registry: {exc!r}") from exc


# ---------------------------------------------------------------- validation

def _column_exists(ref: str, schema_columns: dict[str, list[str]]) -> bool:
    table, _, column = ref.partition(".")
    return column in schema_columns.get(table, [])


def _operand_unit(operand: Operand, metrics: dict[str, MetricDefinition]) -> str | None:
    """Best-effort unit of an operand; None means 'unknown / wildcard'."""
    if operand.kind == "metric" and operand.ref in metrics:
        return metrics[operand.ref].unit
    if operand.kind == "column":
        for m in metrics.values():
            if m.source_column == operand.ref:
                return m.unit
    if operand.kind == "calculation" and operand.calculation is not None:
        calc = operand.calculation
        if calc.op in {"count", "distinct_count"}:
            return "count"
        if calc.op in SAME_UNIT_OPS | {"identity"} and calc.operands:
            return _operand_unit(calc.operands[0], metrics)
        return _inferred_unit(calc, metrics)
    return None


def _inferred_unit(calc: Calculation, metrics: dict[str, MetricDefinition]) -> str | None:
    """Unit implied by the operation alone; None when it cannot be inferred (e.g. multiply)."""
    if calc.op == "share":
        return "fraction"
    if calc.op == "percentage_change":
        return "percent"
    if calc.op == "safe_divide" and len(calc.operands) == 2:
        top, bottom = (_operand_unit(o, metrics) for o in calc.operands)
        return "fraction" if top is not None and top == bottom else None
    if calc.op == "multiply" and len(calc.operands) == 2:
        for first, second in (calc.operands, calc.operands[::-1]):
            if (second.kind == "constant" and _as_decimal(second.ref) == 100
                    and first.calculation is not None
                    and _inferred_unit(first.calculation, metrics) == "fraction"):
                return "percent"
    return None


def _as_decimal(text: str) -> Decimal | None:
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _metric_refs(calc: Calculation) -> Iterator[str]:
    for operand in calc.operands:
        if operand.kind == "metric":
            yield operand.ref
        elif operand.calculation is not None:
            yield from _metric_refs(operand.calculation)


def _cyclic_metrics(metrics: dict[str, MetricDefinition]) -> list[str]:
    """Ids of metrics whose calculation reaches themselves, directly or transitively."""
    graph = {i: set(_metric_refs(m.calculation)) if m.calculation else set() for i, m in metrics.items()}
    cyclic = []
    for start in graph:
        stack, seen = list(graph[start]), set()
        while stack:
            node = stack.pop()
            if node == start:
                cyclic.append(start)
                break
            if node not in seen:
                seen.add(node)
                stack.extend(graph.get(node, ()))
    return cyclic


def _check_calc(metric: MetricDefinition, calc: Calculation, metrics: dict[str, MetricDefinition],
                schema_columns: dict[str, list[str]], problems: list[str]) -> None:
    where = f"metric {metric.id!r}"
    if calc.op not in OPERATIONS:
        problems.append(f"{where}: unknown operation {calc.op!r}")
    for operand in calc.operands:
        if operand.kind == "metric" and operand.ref not in metrics:
            problems.append(f"{where}: operand references unknown metric {operand.ref!r}")
        elif operand.kind == "column" and not _column_exists(operand.ref, schema_columns):
            problems.append(f"{where}: operand references unknown column {operand.ref!r}")
        elif operand.kind == "constant" and _as_decimal(operand.ref) is None:
            problems.append(f"{where}: constant {operand.ref!r} is not a finite Decimal")
        elif operand.kind == "calculation" and operand.calculation is not None:
            _check_calc(metric, operand.calculation, metrics, schema_columns, problems)
    if calc.over == "group":
        if calc.group_population not in GROUP_POPULATIONS:
            problems.append(f"{where}: over group needs group_population in {sorted(GROUP_POPULATIONS)}, "
                            f"got {calc.group_population!r}")
        if (calc.empty_group_value is not None) != (calc.group_population == "full_dimension_members"):
            problems.append(f"{where}: empty_group_value must be present exactly when group_population "
                            "is full_dimension_members")
    if calc.op in SAME_UNIT_OPS:
        operands = calc.operands[:1] if calc.op == "weighted_mean" else calc.operands
        units = {u for u in (_operand_unit(o, metrics) for o in operands) if u not in (None, "none")}
        if len(units) > 1:
            problems.append(f"{where}: incompatible units {sorted(units)} in {calc.op}")
    if calc.op in AGGREGATING_OPS:
        for operand in calc.operands:
            aggregated = operand.kind == "metric" and operand.ref in metrics \
                and metrics[operand.ref].grain in AGGREGATE_GRAINS
            if aggregated and (calc.over != "group" or metric.grain != "group"):
                problems.append(f"{where}: {calc.op} over already-aggregated {operand.ref!r} "
                                "must use over: group and declare grain 'group'")
            if operand.kind == "column":
                table = operand.ref.partition(".")[0]
                if calc.over != TABLE_GRAINS.get(table):
                    problems.append(f"{where}: {calc.op} of {operand.ref!r} must be over "
                                    f"{TABLE_GRAINS.get(table)!r}, not {calc.over!r}")


def validate_registry(registry: MetricRegistry, schema_columns: dict[str, list[str]],
                      numeric_columns: list[str] | None = None) -> list[str]:
    """Return human-readable problems; an empty list means the registry is consistent.

    ``numeric_columns`` (``table.column``) must each be registered as a base measure.
    """
    problems: list[str] = []
    seen: set[str] = set()
    for item_id in [m.id for m in registry.metrics] + [d.id for d in registry.dimensions]:
        if item_id in seen:
            problems.append(f"duplicate id {item_id!r}")
        seen.add(item_id)
    metrics = {m.id: m for m in registry.metrics}
    dimension_ids = {d.id for d in registry.dimensions}
    key_columns = {f"{t}.{k}" for t, k in registry.primary_keys.items()} | {
        c for j in registry.joins for c in (j.from_column, j.to_column)}
    for metric_id in _cyclic_metrics(metrics):
        problems.append(f"metric {metric_id!r}: calculation references itself (cycle)")

    for m in registry.metrics:
        if m.unit not in UNITS:
            problems.append(f"metric {m.id!r}: unknown unit {m.unit!r}")
        if m.unit != "none" and "precision" not in m.display:
            problems.append(f"metric {m.id!r}: {m.unit} metric without display precision")
        if m.grain not in GRAINS:
            problems.append(f"metric {m.id!r}: unknown grain {m.grain!r}")
        if m.default_aggregation is not None and m.default_aggregation not in OPERATIONS:
            problems.append(f"metric {m.id!r}: unknown default aggregation {m.default_aggregation!r}")
        for dim in m.allowed_dimensions:
            if dim not in dimension_ids:
                problems.append(f"metric {m.id!r}: allowed dimension {dim!r} is not a known dimension")
        for op in m.allowed_operations:
            if op not in OPERATIONS:
                problems.append(f"metric {m.id!r}: unknown allowed operation {op!r}")
        if (m.source_column is None) == (m.calculation is None):
            problems.append(f"metric {m.id!r}: needs exactly one of source column or calculation")
        if m.source_column and not _column_exists(m.source_column, schema_columns):
            problems.append(f"metric {m.id!r}: source column {m.source_column!r} not in schema")
        if m.calculation is not None:
            _check_calc(m, m.calculation, metrics, schema_columns, problems)
            implied = _inferred_unit(m.calculation, metrics)
            if implied is not None and implied != m.unit:
                problems.append(f"metric {m.id!r}: declared unit {m.unit!r} but calculation implies {implied!r}")
        if m.source_column in key_columns:
            problems.append(f"metric {m.id!r}: base measure on key column {m.source_column!r}")
        for ref in (m.numerator, m.denominator):
            if ref is not None and ref not in metrics:
                problems.append(f"metric {m.id!r}: numerator/denominator {ref!r} is not a metric")

    base_columns = {m.source_column for m in registry.metrics if m.source_column}
    for column in numeric_columns or []:
        if column not in base_columns:
            problems.append(f"numeric column {column!r} is not registered as a base measure")
    for d in registry.dimensions:
        if not _column_exists(d.source, schema_columns):
            problems.append(f"dimension {d.id!r}: source column {d.source!r} not in schema")
    for j in registry.joins:
        for ref in (j.from_column, j.to_column):
            if not _column_exists(ref, schema_columns):
                problems.append(f"join {j.id!r}: column {ref!r} not in schema")

    for phrase, candidates in registry.ambiguous_phrases.items():
        for candidate in candidates:
            if candidate not in metrics:
                problems.append(f"ambiguous phrase {phrase!r} names unknown metric {candidate!r}")
    owners: dict[str, set[str]] = {}
    for m in registry.metrics:
        for name in (m.id, m.label, *m.synonyms):
            owners.setdefault(_norm(name), set()).add(m.id)
    for name, ids in owners.items():
        if len(ids) > 1 and name not in registry.ambiguous_phrases:
            problems.append(f"name {name!r} (id, label or synonym) shared by {sorted(ids)} "
                            "without an ambiguous_phrases entry")
    for example in registry.examples:
        if example.get("metric") not in metrics:
            problems.append(f"example names unknown metric {example.get('metric')!r}")
    return problems
