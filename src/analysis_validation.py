"""Deterministic calculation gate for analysis-model answer contracts (Phase 4).

``validate_answer`` takes a parsed ``AnswerContract``, the engine-owned
``ResultManifest`` and the metric registry, and decides pass/reject for the WHOLE
answer. It never trusts a model number: every claim is re-evaluated with
``Decimal`` from manifest cells only, through a fixed set of typed operations (no
expressions, no ``eval``, no model-supplied constants), then compared at the
registry display precision with ``ROUND_HALF_UP`` (DECISIONS.md #3).

Fail-closed rules (DECISIONS.md #7, #9): a null operand, a zero denominator, an
empty population or a zero percentage-change baseline is undefined, never 0.

Nested calculations: ``ClaimCalculation.inputs`` is typed as ``ClaimInput`` in
``src/analysis_contracts.py`` and its parser only emits flat inputs. This gate
additionally accepts a ``ClaimCalculation`` in an input position (bounded to
depth 3) for directly constructed contracts; anything else in an input position
is treated as a model-supplied constant and rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, DivisionByZero, InvalidOperation, localcontext
from typing import Any, Literal

from src.analysis_contracts import AnswerContract, Claim, ClaimCalculation, ClaimInput
from src.semantic_query import MetricBinding, ResultManifest
from src.semantics.registry import MetricDefinition, MetricRegistry

DECIMAL_PRECISION = 28
MAX_CLAIMS = 50
MAX_DEPTH = 3
MAX_NODES = 16
GROUP_COVERAGE_MAX_ROWS = 12
COMPLETE_SCOPE = "complete_group_population"
INCOMPLETE_LIMITATION = "population_incomplete"
SUBTOTAL_LABELS = frozenset({"total", "grand total", "subtotal", "all", "overall"})

COLUMN_OPS = frozenset({"sum", "count", "distinct_count", "mean", "min", "max"})
BINARY_OPS = frozenset({"add", "subtract", "multiply", "safe_divide", "percentage_change"})
AGGREGATING_OPS = COLUMN_OPS | {"weighted_mean"}
SELECTION_OPS = frozenset({"identity", "min", "max"})
SAME_UNIT_OPS = frozenset({"add", "subtract", "sum", "mean", "min", "max", "weighted_mean",
                           "share", "percentage_change"})
ADDITIVE_UNITS = frozenset({"money", "count", "points"})

# Stable reason codes (asserted by tests; do not rename without updating callers).
REASON_CODES = frozenset({
    "too_many_claims", "duplicate_claim_id", "depth_exceeded", "calculation_too_large",
    "unknown_metric", "operation_not_allowed", "metric_binding_mismatch", "unit_mismatch",
    "model_constant", "unknown_result_id", "unknown_binding", "unknown_row_ref", "missing_cell",
    "row_ref_required", "invalid_arity", "invalid_operand_shape", "missing_scope", "invalid_scope",
    "null_cell", "non_numeric_cell", "non_finite_cell", "dimension_as_number", "undefined_value",
    "empty_population", "incomplete_population", "subtotal_row_in_population", "duplicate_group_key",
    "invalid_reported_value", "imprecise_value", "value_mismatch", "missing_entity_ref",
    "entity_mismatch", "missing_population_limitation", "missing_direct_answer",
    "missing_group_coverage", "scalar_not_identity", "irrelevant_number",
    "question_metric_not_answered", "result_id_mismatch", "invalid_share_denominator",
})

_DECIMAL_TEXT = re.compile(r"-?[0-9]+(\.[0-9]+)?")


@dataclass(frozen=True)
class EvaluationError:
    reason_code: str
    message: str


@dataclass(frozen=True)
class ClaimCheck:
    claim_id: str
    metric_id: str
    op: str
    recomputed_value: str | None
    reported_value: str
    display_value: str | None
    ok: bool
    reason_code: str | None


@dataclass(frozen=True)
class GateVerdict:
    status: Literal["pass", "reject"]
    reason_code: str | None
    message: str
    claim_id: str | None
    checked_claims: tuple[ClaimCheck, ...]


class _Fail(Exception):
    def __init__(self, code: str, message: str) -> None:
        assert code in REASON_CODES, code
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------- rounding

def round_half_up(value: Decimal, precision: int) -> Decimal:
    """Quantize to ``precision`` decimals with ties away from zero (DECISIONS.md #3)."""
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION + 10
        return value.quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP)


def _metric(metric: MetricDefinition | str, registry: MetricRegistry) -> MetricDefinition:
    return metric if isinstance(metric, MetricDefinition) else registry.lookup(metric)


def display_precision(metric: MetricDefinition | str, registry: MetricRegistry) -> int:
    m = _metric(metric, registry)
    precision = m.display.get("precision")
    if not isinstance(precision, int) or precision < 0:
        raise ValueError(f"metric {m.id!r} has no display precision")
    return precision


def format_display(value: Decimal, metric: MetricDefinition | str, registry: MetricRegistry) -> str:
    """Render ``value`` at the registry display precision with ROUND_HALF_UP."""
    return str(round_half_up(value, display_precision(metric, registry)))


# --------------------------------------------------------------------------- cells

def _to_decimal(value: Any, where: str) -> Decimal:
    if value is None:
        raise _Fail("null_cell", f"{where} is null")
    if isinstance(value, bool):
        raise _Fail("non_numeric_cell", f"{where} is a boolean")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(str(value))
    elif isinstance(value, float):
        result = Decimal(repr(value))
    elif isinstance(value, str):
        if not _DECIMAL_TEXT.fullmatch(value.strip()):
            raise _Fail("non_numeric_cell", f"{where} is not plain decimal text")
        result = Decimal(value.strip())
    else:
        raise _Fail("non_numeric_cell", f"{where} has non-numeric type {type(value).__name__}")
    if not result.is_finite():
        raise _Fail("non_finite_cell", f"{where} is not finite")
    return result


def _binding(manifest: ResultManifest, inp: ClaimInput) -> MetricBinding:
    if inp.result_id != manifest.result_id:
        raise _Fail("unknown_result_id", f"input names result {inp.result_id!r}")
    for b in manifest.bindings:
        if b.binding_id == inp.binding_id:
            return b
    raise _Fail("unknown_binding", f"binding {inp.binding_id!r} is not in the manifest")


def _check_input_type(inp: Any) -> None:
    if not isinstance(inp, (ClaimInput, ClaimCalculation)):
        raise _Fail("model_constant", f"input {inp!r} is not a manifest reference")


def _cell(manifest: ResultManifest, inp: ClaimInput, *, numeric: bool = True) -> Any:
    b = _binding(manifest, inp)
    if inp.row_ref is None:
        raise _Fail("row_ref_required", f"input {inp.binding_id!r} needs a row_ref")
    if inp.row_ref not in manifest.row_refs:
        raise _Fail("unknown_row_ref", f"row ref {inp.row_ref!r} is not in the manifest")
    row = manifest.row_refs[inp.row_ref]
    if inp.binding_id not in row:
        raise _Fail("missing_cell", f"row {inp.row_ref!r} has no {inp.binding_id!r}")
    if numeric and b.kind != "metric":
        raise _Fail("dimension_as_number", f"dimension binding {inp.binding_id!r} used as a number")
    return _to_decimal(row[inp.binding_id], f"{inp.row_ref}.{inp.binding_id}") if numeric else row[inp.binding_id]


def _dimension_ids(manifest: ResultManifest) -> tuple[str, ...]:
    return tuple(b.binding_id for b in manifest.bindings if b.kind == "dimension")


def _check_population(manifest: ResultManifest) -> None:
    """Column aggregation needs a complete, detail-only, key-unique population."""
    if manifest.completeness != "complete":
        raise _Fail("incomplete_population", f"result completeness is {manifest.completeness!r}")
    dims = _dimension_ids(manifest)
    if not dims:
        return
    seen: set[tuple[str, ...]] = set()
    for ref, row in manifest.row_refs.items():
        key = tuple("" if row.get(d) is None else str(row.get(d)) for d in dims)
        if any(row.get(d) is None or str(row.get(d)).strip().lower() in SUBTOTAL_LABELS for d in dims):
            raise _Fail("subtotal_row_in_population", f"row {ref!r} looks like a subtotal/null group")
        if key in seen:
            raise _Fail("duplicate_group_key", f"group key {key!r} repeats (duplicated join?)")
        seen.add(key)


def _column(manifest: ResultManifest, inp: Any, *, numeric: bool = True) -> list[Any]:
    if not isinstance(inp, ClaimInput) or inp.row_ref is not None:
        raise _Fail("invalid_operand_shape", "an aggregate needs a whole-column input (no row_ref)")
    b = _binding(manifest, inp)
    if inp.scope is None:
        raise _Fail("missing_scope", f"column input {inp.binding_id!r} needs scope {COMPLETE_SCOPE!r}")
    if inp.scope != COMPLETE_SCOPE:
        raise _Fail("invalid_scope", f"scope {inp.scope!r} is not {COMPLETE_SCOPE!r}")
    _check_population(manifest)
    if numeric and b.kind != "metric":
        raise _Fail("dimension_as_number", f"dimension binding {inp.binding_id!r} used as a number")
    values = []
    for ref, row in manifest.row_refs.items():
        if inp.binding_id not in row:
            raise _Fail("missing_cell", f"row {ref!r} has no {inp.binding_id!r}")
        cell = row[inp.binding_id]
        if numeric:
            values.append(_to_decimal(cell, f"{ref}.{inp.binding_id}"))
        else:
            if cell is None:
                raise _Fail("null_cell", f"{ref}.{inp.binding_id} is null")
            values.append(cell)
    if not values:
        raise _Fail("empty_population", "the result has no rows")
    return values


# --------------------------------------------------------------------------- evaluator

def _depth(calc: ClaimCalculation) -> int:
    return 1 + max((_depth(i) for i in calc.inputs if isinstance(i, ClaimCalculation)), default=0)


def _nodes(calc: ClaimCalculation) -> int:
    return sum(_nodes(i) if isinstance(i, ClaimCalculation) else 1 for i in calc.inputs) + 1


def _scalar(inp: Any, manifest: ResultManifest, depth: int) -> Decimal:
    _check_input_type(inp)
    if isinstance(inp, ClaimCalculation):
        return _eval(inp, manifest, depth + 1)
    return _cell(manifest, inp)


def _arity(calc: ClaimCalculation, *allowed: int) -> None:
    if len(calc.inputs) not in allowed:
        raise _Fail("invalid_arity", f"{calc.op} takes {' or '.join(map(str, allowed))} inputs, got {len(calc.inputs)}")


def _div(num: Decimal, den: Decimal) -> Decimal:
    if den == 0:
        raise _Fail("undefined_value", "zero denominator: the value is undefined, not 0")
    return num / den


def _eval(calc: ClaimCalculation, manifest: ResultManifest, depth: int) -> Decimal:
    if depth > MAX_DEPTH:
        raise _Fail("depth_exceeded", f"calculation depth exceeds {MAX_DEPTH}")
    for inp in calc.inputs:
        _check_input_type(inp)
    op = calc.op
    if op == "identity":
        _arity(calc, 1)
        return _scalar(calc.inputs[0], manifest, depth)
    if op in BINARY_OPS:
        _arity(calc, 2)
        a, b = (_scalar(i, manifest, depth) for i in calc.inputs)
        if op == "add":
            return a + b
        if op == "subtract":
            return a - b
        if op == "multiply":
            return a * b
        if op == "safe_divide":
            return _div(a, b)
        if b == 0:  # percentage_change
            raise _Fail("undefined_value", "zero percentage-change baseline: undefined")
        return (a - b) / b * 100
    if op in COLUMN_OPS:
        _arity(calc, 1)
        if op in ("count", "distinct_count"):
            values = _column(manifest, calc.inputs[0], numeric=False)
            return Decimal(len(values) if op == "count" else len({str(v) for v in values}))
        values = _column(manifest, calc.inputs[0])
        if op == "sum":
            return sum(values, Decimal(0))
        if op == "mean":
            return sum(values, Decimal(0)) / len(values)
        return min(values) if op == "min" else max(values)
    if op == "weighted_mean":
        _arity(calc, 2)
        values = _column(manifest, calc.inputs[0])
        weights = _column(manifest, calc.inputs[1])
        return _div(sum((v * w for v, w in zip(values, weights)), Decimal(0)), sum(weights, Decimal(0)))
    if op == "share":
        _arity(calc, 1, 2)
        part = _scalar(calc.inputs[0], manifest, depth)
        if len(calc.inputs) == 1:
            first = calc.inputs[0]
            if not isinstance(first, ClaimInput):
                raise _Fail("invalid_operand_shape", "a one-input share needs a cell input")
            whole_input = ClaimInput(first.result_id, first.binding_id, None, COMPLETE_SCOPE)
            whole = sum(_column(manifest, whole_input), Decimal(0))
        else:
            den = calc.inputs[1]
            if isinstance(den, ClaimInput) and den.row_ref is None:
                whole = sum(_column(manifest, den), Decimal(0))
            else:
                whole = _scalar(den, manifest, depth)
        return _div(part, whole)
    raise _Fail("operation_not_allowed", f"operation {op!r} is not supported")


def evaluate_claim(claim: Claim, manifest: ResultManifest,
                   registry: MetricRegistry) -> Decimal | EvaluationError:
    """Recompute ``claim`` from manifest cells only. ``registry`` is unused by arithmetic
    but kept in the signature so registry-owned constants can be added later."""
    del registry
    try:
        if _nodes(claim.calculation) > MAX_NODES:
            raise _Fail("calculation_too_large", f"calculation has more than {MAX_NODES} nodes")
        with localcontext() as ctx:
            ctx.prec = DECIMAL_PRECISION
            ctx.traps[InvalidOperation] = True
            ctx.traps[DivisionByZero] = True
            value = _eval(claim.calculation, manifest, 1)
        if not value.is_finite():  # pragma: no cover - traps already raise
            raise _Fail("non_finite_cell", "result is not finite")
        return value
    except _Fail as exc:
        return EvaluationError(exc.code, exc.message)
    except (InvalidOperation, DivisionByZero) as exc:
        return EvaluationError("undefined_value", f"arithmetic is undefined: {exc!r}")
    except RecursionError:
        return EvaluationError("depth_exceeded", "calculation is too deep")


# --------------------------------------------------------------------------- compatibility

def _input_bindings(calc: ClaimCalculation, manifest: ResultManifest) -> list[MetricBinding]:
    found: list[MetricBinding] = []
    for inp in calc.inputs:
        _check_input_type(inp)
        if isinstance(inp, ClaimCalculation):
            found.extend(_input_bindings(inp, manifest))
        else:
            found.append(_binding(manifest, inp))
    return found


def _unit(inp: Any, manifest: ResultManifest, registry: MetricRegistry) -> str | None:
    if isinstance(inp, ClaimCalculation):
        return _calc_unit(inp, manifest, registry)
    b = _binding(manifest, inp)
    if b.kind != "metric":
        return None
    return b.unit or registry.lookup(b.ref_id).unit


def _calc_unit(calc: ClaimCalculation, manifest: ResultManifest, registry: MetricRegistry) -> str:
    op = calc.op
    units = [_unit(i, manifest, registry) for i in calc.inputs]
    if op in ("count", "distinct_count"):
        return "count"
    if op in ("safe_divide", "share"):
        return "fraction"
    if op == "multiply":
        raise _Fail("operation_not_allowed", "multiply needs a registry-owned constant; none can be named")
    value_units = units[:1] if op == "weighted_mean" else units
    if any(u is None for u in value_units):
        raise _Fail("dimension_as_number", f"{op} has a dimension operand")
    if op in SAME_UNIT_OPS and len(set(value_units)) > 1:
        raise _Fail("unit_mismatch", f"{op} mixes units {sorted(set(map(str, value_units)))}")
    if op == "percentage_change":
        return "percent"
    return str(value_units[0])


def _template_matches(metric: MetricDefinition, calc: ClaimCalculation, bindings: list[MetricBinding]) -> bool:
    """The claim metric is a registry shape for this op over these input metrics."""
    reg = metric.calculation
    if reg is None or reg.op != calc.op:
        return False
    refs = [o.ref for o in reg.operands]
    kinds = {o.kind for o in reg.operands}
    if kinds == {"parameter"}:
        return True
    if kinds != {"metric"} or any(isinstance(i, ClaimCalculation) for i in calc.inputs):
        return False
    return refs == [b.ref_id for b in bindings][:len(refs)]


def _check_share_shape(calc: ClaimCalculation) -> None:
    """Part and whole must share one binding; the whole is the full complete-population column."""
    part = calc.inputs[0] if calc.inputs else None
    if not isinstance(part, ClaimInput) or part.row_ref is None:
        raise _Fail("invalid_share_denominator", "a share part must be a single cell input")
    if len(calc.inputs) == 1:
        return
    whole = calc.inputs[1] if len(calc.inputs) > 1 else None
    if not isinstance(whole, ClaimInput) or whole.row_ref is not None:
        raise _Fail("invalid_share_denominator", "a share whole must be the full column, not a cell")
    if whole.binding_id != part.binding_id:
        raise _Fail("invalid_share_denominator", "share part and whole must use the same binding")
    if whole.scope != COMPLETE_SCOPE:
        raise _Fail("invalid_share_denominator", f"share whole needs scope {COMPLETE_SCOPE!r}")


def _count_matches(metric: MetricDefinition, registry: MetricRegistry, bindings: list[MetricBinding]) -> bool:
    metric_bindings = [b for b in bindings if b.kind == "metric"]
    if metric_bindings and all(b.ref_id == metric.id for b in metric_bindings):
        return True
    reg = metric.calculation
    if reg is None or reg.op not in ("count", "distinct_count") or not metric_bindings:
        return False
    columns = {o.ref for o in reg.operands if o.kind == "column"}
    for b in metric_bindings:
        try:
            source = registry.lookup(b.ref_id).source_column
        except KeyError:
            return False
        if source is None or source not in columns:
            return False
    return True


def _check_compatibility(claim: Claim, manifest: ResultManifest, registry: MetricRegistry) -> None:
    calc = claim.calculation
    try:
        metric = registry.lookup(claim.metric_id)
    except KeyError as exc:
        raise _Fail("unknown_metric", f"metric {claim.metric_id!r} is not registered") from exc
    bindings = _input_bindings(calc, manifest)
    if bindings and all(b.kind == "dimension" for b in bindings):
        raise _Fail("irrelevant_number", "a claim over dimension columns only is not a measure")
    op = calc.op
    if op == "share":
        _check_share_shape(calc)
    if op in ("count", "distinct_count") and metric.unit != "count":
        raise _Fail("irrelevant_number", f"a row count cannot answer {metric.unit} metric {metric.id!r}")
    produced = _calc_unit(calc, manifest, registry)
    metric_ids = {b.ref_id for b in bindings if b.kind == "metric"}
    if _template_matches(metric, calc, bindings):
        if calc.op == "subtract" and len(metric_ids) > 1:
            raise _Fail("metric_binding_mismatch",
                        f"{op} operands bind different metrics {sorted(metric_ids)}; a difference needs one metric")
        allowed = True
    elif op in SELECTION_OPS:
        if metric_ids != {metric.id}:
            raise _Fail("metric_binding_mismatch",
                        f"{op} input binds {sorted(metric_ids)}, not claim metric {metric.id!r}")
        allowed = True
    elif op in ("count", "distinct_count"):
        if not _count_matches(metric, registry, bindings):
            raise _Fail("metric_binding_mismatch",
                        f"{op} over {sorted(metric_ids)} does not produce claim metric {metric.id!r}")
        allowed = True
    elif metric_ids == {metric.id}:
        allowed = op in metric.allowed_operations or (op in ("sum", "add") and metric.unit in ADDITIVE_UNITS
                                                      and "share" in metric.allowed_operations)
    else:
        raise _Fail("metric_binding_mismatch",
                    f"{op} over {sorted(metric_ids)} does not produce claim metric {metric.id!r}")
    if not allowed:
        raise _Fail("operation_not_allowed", f"{op} is not allowed for metric {metric.id!r}")
    expected = metric.unit
    if metric.unit == "none":  # template such as difference: unit is the operands' unit
        expected = produced
    if produced != expected:
        raise _Fail("unit_mismatch", f"{op} produces {produced!r} but metric {metric.id!r} is {metric.unit!r}")


def _aggregates_column(calc: ClaimCalculation) -> bool:
    if calc.op in AGGREGATING_OPS:
        return True
    if calc.op == "share":
        if len(calc.inputs) == 1:
            return True
        den = calc.inputs[1]
        if isinstance(den, ClaimInput) and den.row_ref is None:
            return True
    return any(isinstance(i, ClaimCalculation) and _aggregates_column(i) for i in calc.inputs)


def _check_scope(claim: Claim, manifest: ResultManifest) -> None:
    if manifest.completeness != "complete" and (claim.calculation.op != "identity"
                                                or _aggregates_column(claim.calculation)):
        raise _Fail("incomplete_population",
                    f"result completeness is {manifest.completeness!r}: only identity claims on rows are allowed")


# --------------------------------------------------------------------------- comparison

def _compare(claim: Claim, value: Decimal, registry: MetricRegistry) -> str:
    text = claim.reported_value
    if not isinstance(text, str) or not _DECIMAL_TEXT.fullmatch(text):
        raise _Fail("invalid_reported_value", f"reported_value {text!r} is not plain decimal text")
    precision = display_precision(claim.metric_id, registry)
    display = round_half_up(value, precision)
    reported = Decimal(text)
    decimals = len(text.split(".")[1]) if "." in text else 0
    if decimals < precision:
        raise _Fail("imprecise_value", f"reported {text} has fewer than {precision} decimals")
    if decimals == precision:
        ok = reported == display
    else:
        ok = reported == round_half_up(value, decimals) and round_half_up(reported, precision) == display
    if not ok:
        raise _Fail("value_mismatch", f"reported {text} but the recomputed value displays as {display}")
    return str(display)


def _entity_rows(claim: Claim, manifest: ResultManifest, value: Decimal) -> list[str] | None:
    """Row refs the claim's number belongs to, or None when attribution does not apply."""
    calc = claim.calculation
    first = calc.inputs[0] if calc.inputs else None
    if calc.op in ("identity", "share") and isinstance(first, ClaimInput) and first.row_ref is not None:
        return [first.row_ref]
    if calc.op in ("min", "max") and isinstance(first, ClaimInput):
        return [ref for ref, row in manifest.row_refs.items()
                if _to_decimal(row.get(first.binding_id), ref) == value]
    return None


def _check_entity(claim: Claim, manifest: ResultManifest, value: Decimal) -> None:
    dims = _dimension_ids(manifest)
    if not dims:
        return
    rows = _entity_rows(claim, manifest, value)
    if rows is None:
        named = {d: claim.entity_refs[d] for d in dims if d in claim.entity_refs}
        if not named:
            return
        if _aggregates_column(claim.calculation):
            raise _Fail("entity_mismatch", f"a column aggregate cannot be attributed to {named}; only population_id is allowed")
        for ref in sorted(_calc_row_refs(claim.calculation)):
            row = manifest.row_refs.get(ref, {})
            for d, v in named.items():
                if v != _cell_text(row.get(d)):
                    raise _Fail("entity_mismatch", f"input row {ref!r} is not {d}={v!r}")
        return
    missing = [d for d in dims if d not in claim.entity_refs]
    if missing:
        raise _Fail("missing_entity_ref", f"claim must name its entity for {missing}")
    for ref in rows:
        row = manifest.row_refs[ref]
        if all(claim.entity_refs[d] == _cell_text(row.get(d)) for d in dims):
            return
    raise _Fail("entity_mismatch", f"entity {dict((d, claim.entity_refs[d]) for d in dims)} is not the row the value comes from")


def _calc_row_refs(calc: ClaimCalculation) -> set[str]:
    out: set[str] = set()
    for inp in calc.inputs:
        if isinstance(inp, ClaimCalculation):
            out |= _calc_row_refs(inp)
        elif isinstance(inp, ClaimInput) and inp.row_ref is not None:
            out.add(inp.row_ref)
    return out


def _cell_text(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, float):
        return str(Decimal(repr(value)))
    return str(value)


# --------------------------------------------------------------------------- coverage

def _referenced_bindings(calc: ClaimCalculation) -> set[str]:
    out: set[str] = set()
    for inp in calc.inputs:
        if isinstance(inp, ClaimCalculation):
            out |= _referenced_bindings(inp)
        elif isinstance(inp, ClaimInput):
            out.add(inp.binding_id)
    return out


def _referenced_rows(claim: Claim, manifest: ResultManifest) -> set[str]:
    """Rows in calculation inputs only; evidence_refs never count toward coverage."""
    del manifest
    return _calc_row_refs(claim.calculation)


def _check_coverage(contract: AnswerContract, manifest: ResultManifest,
                    question_metric_ids: tuple[str, ...] | list[str] | None) -> None:
    direct = [c for c in contract.claims if c.answer_role == "direct_answer"]
    if not direct:
        raise _Fail("missing_direct_answer", "the answer has no direct_answer claim")
    for b in manifest.bindings:
        if b.kind == "metric" and not any(b.binding_id in _referenced_bindings(c.calculation) for c in direct):
            raise _Fail("missing_direct_answer", f"no direct_answer claim uses binding {b.binding_id!r}")
    dims = _dimension_ids(manifest)
    if not dims:
        for c in direct:
            if c.calculation.op != "identity" or any(isinstance(i, ClaimCalculation) for i in c.calculation.inputs):
                raise _Fail("scalar_not_identity", f"claim {c.claim_id!r} must be an identity of the scalar cell")
    elif manifest.completeness == "complete" and manifest.row_count <= GROUP_COVERAGE_MAX_ROWS:
        covered: set[str] = set()
        for c in contract.claims:
            covered |= _referenced_rows(c, manifest)
        missing = sorted(set(manifest.row_refs) - covered)
        if missing:
            raise _Fail("missing_group_coverage", f"rows {missing} are not referenced by any claim")
    if question_metric_ids:
        answered = {c.metric_id for c in direct}
        for metric_id in question_metric_ids:
            if metric_id not in answered:
                raise _Fail("question_metric_not_answered", f"question metric {metric_id!r} has no direct answer")


# --------------------------------------------------------------------------- gate

def _reject(code: str, message: str, claim_id: str | None, checks: list[ClaimCheck]) -> GateVerdict:
    return GateVerdict("reject", code, message, claim_id, tuple(checks))


def validate_answer(contract: AnswerContract, manifest: ResultManifest, registry: MetricRegistry, *,
                    question_metric_ids: tuple[str, ...] | list[str]) -> GateVerdict:
    """Pass only if every claim recomputes, is attributed correctly and the answer covers the result."""
    checks: list[ClaimCheck] = []
    if contract.result_id != manifest.result_id or contract.request_id != manifest.request_id:
        return _reject("result_id_mismatch", "contract is not bound to this manifest", None, checks)
    if len(contract.claims) > MAX_CLAIMS:
        return _reject("too_many_claims", f"more than {MAX_CLAIMS} claims", None, checks)
    seen: set[str] = set()
    for claim in contract.claims:
        if claim.claim_id in seen:
            return _reject("duplicate_claim_id", f"claim id {claim.claim_id!r} repeats", claim.claim_id, checks)
        seen.add(claim.claim_id)

    for claim in contract.claims:
        recomputed: Decimal | None = None
        display: str | None = None
        try:
            if _depth(claim.calculation) > MAX_DEPTH:
                raise _Fail("depth_exceeded", f"calculation depth exceeds {MAX_DEPTH}")
            _check_compatibility(claim, manifest, registry)          # (a)
            _check_scope(claim, manifest)                            # (e) before arithmetic
            result = evaluate_claim(claim, manifest, registry)       # (b)
            if isinstance(result, EvaluationError):
                raise _Fail(result.reason_code, result.message)
            recomputed = result
            display = _compare(claim, recomputed, registry)          # (c)
            _check_entity(claim, manifest, recomputed)               # (d)
        except _Fail as exc:
            checks.append(ClaimCheck(claim.claim_id, claim.metric_id, claim.calculation.op,
                                     None if recomputed is None else str(recomputed), claim.reported_value,
                                     display, False, exc.code))
            return _reject(exc.code, exc.message, claim.claim_id, checks)
        checks.append(ClaimCheck(claim.claim_id, claim.metric_id, claim.calculation.op, str(recomputed),
                                 claim.reported_value, display, True, None))

    try:
        if manifest.completeness != "complete" and INCOMPLETE_LIMITATION not in contract.limitations:
            raise _Fail("missing_population_limitation",
                        f"an incomplete result needs the {INCOMPLETE_LIMITATION!r} limitation")
        _check_coverage(contract, manifest, question_metric_ids)    # (f)
    except _Fail as exc:
        return _reject(exc.code, exc.message, None, checks)
    return GateVerdict("pass", None, f"{len(checks)} claims verified", None, tuple(checks))
