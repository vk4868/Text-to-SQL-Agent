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

Data flow and trust (read this first):
  * Inputs: an ``AnswerContract`` (model-proposed, parsed by
    ``src/analysis_contracts.py``), a ``ResultManifest`` (engine-owned, built by
    ``src/semantic_query.bind_result`` from the executed SQL result) and a
    ``MetricRegistry`` (``src/semantics/registry.py``). Only the contract is untrusted.
  * Output: one ``GateVerdict`` (pass, or reject with ONE stable reason code from
    ``REASON_CODES``). A reject rejects the whole answer; there is no partial pass and
    no later component (e.g. the Phase 5 verifier) may override it.
  * Per claim the checks run in this order: depth, compatibility (a), scope (e),
    recomputation (b), comparison to the reported value (c), entity attribution (d).
    After all claims: population limitation, then coverage (f).

Module layout: constants, result dataclasses, ``_Fail``, rounding helpers, cell and
population access, the evaluator, unit/metric compatibility, scope, comparison and
entity attribution, coverage, and finally the ``validate_answer`` entry point.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, DivisionByZero, InvalidOperation, localcontext
from typing import Any, Literal

from src.analysis_contracts import AnswerContract, Claim, ClaimCalculation, ClaimInput
from src.semantic_query import MetricBinding, ResultManifest
from src.semantics.registry import MetricDefinition, MetricRegistry

# --- Numeric and size limits -------------------------------------------------------------
# What: fixed engine limits. Inputs: none (module constants, never model-supplied).
# Output: bounds used by the evaluator and gate.
#   DECIMAL_PRECISION   significant digits for Decimal arithmetic (28).
#   MAX_CLAIMS          claims allowed per answer          -> "too_many_claims".
#   MAX_DEPTH           calculation nesting allowed        -> "depth_exceeded".
#   MAX_NODES           inputs+ops per calculation         -> "calculation_too_large".
#   GROUP_COVERAGE_MAX_ROWS  grouped results up to this many rows must have every row
#                       referenced by a claim              -> "missing_group_coverage".
DECIMAL_PRECISION = 28
MAX_CLAIMS = 50
MAX_DEPTH = 3
MAX_NODES = 16
GROUP_COVERAGE_MAX_ROWS = 12

# --- Population tokens -------------------------------------------------------------------
# What: the only scope string a whole-column input may carry, the limitation string an
# answer over an incomplete result must declare, and the group labels treated as
# subtotal rows. Inputs: compared against model-supplied ClaimInput.scope /
# AnswerContract.limitations and manifest dimension cells. Output: drives
# "missing_scope", "invalid_scope", "missing_population_limitation" and
# "subtotal_row_in_population".
COMPLETE_SCOPE = "complete_group_population"
INCOMPLETE_LIMITATION = "population_incomplete"
SUBTOTAL_LABELS = frozenset({"total", "grand total", "subtotal", "all", "overall"})

# --- Operation vocabularies --------------------------------------------------------------
# What: the closed set of typed operations a claim may use (model-proposed op names are
# looked up here; anything else -> "operation_not_allowed"). Inputs: none (constants).
# Output: set membership used by _eval, _calc_unit and _check_compatibility.
#   COLUMN_OPS       aggregate one whole column (needs scope + complete population).
#   BINARY_OPS       exactly two scalar operands.
#   AGGREGATING_OPS  every op that reads a whole column (COLUMN_OPS + weighted_mean).
#   SELECTION_OPS    ops that return one existing cell's value (no new number).
#   SAME_UNIT_OPS    ops whose value operands must share one unit -> "unit_mismatch".
#   ADDITIVE_UNITS   units whose metrics may be summed/added.
COLUMN_OPS = frozenset({"sum", "count", "distinct_count", "mean", "min", "max"})
BINARY_OPS = frozenset({"add", "subtract", "multiply", "safe_divide", "percentage_change"})
AGGREGATING_OPS = COLUMN_OPS | {"weighted_mean"}
SELECTION_OPS = frozenset({"identity", "min", "max"})
SAME_UNIT_OPS = frozenset({"add", "subtract", "sum", "mean", "min", "max", "weighted_mean",
                           "share", "percentage_change"})
ADDITIVE_UNITS = frozenset({"money", "count", "points"})

# --- Reason codes ------------------------------------------------------------------------
# What: the complete, stable vocabulary of rejection reason codes (asserted by tests; do
# not rename without updating callers). Inputs: none. Output: every _Fail, EvaluationError,
# ClaimCheck.reason_code and GateVerdict.reason_code uses a member of this set; _Fail
# asserts membership so an unregistered code cannot be raised.
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

# What: pattern for plain decimal text (optional minus, digits, optional fraction). No
# exponent, no thousands separator, no currency symbol, no "NaN"/"Infinity".
# Inputs: a string (model reported_value or a text manifest cell). Output: used with
# fullmatch; a miss -> "invalid_reported_value" / "non_numeric_cell".
_DECIMAL_TEXT = re.compile(r"-?[0-9]+(\.[0-9]+)?")


@dataclass(frozen=True)
class EvaluationError:
    """Why ``evaluate_claim`` could not produce a number (the non-exception result).

    Inputs: built by ``evaluate_claim`` from a caught ``_Fail`` or arithmetic error.
    Returns/holds: ``reason_code`` (a member of ``REASON_CODES``) and a human message.
    """

    reason_code: str
    message: str


@dataclass(frozen=True)
class ClaimCheck:
    """Audit record for one claim, in the order the claim was checked.

    Inputs: built by ``validate_answer`` from the contract's ``Claim`` (claim_id,
    metric_id, op, reported_value) and the gate's own recomputation.
    Holds: ``recomputed_value`` (Decimal text or None when it could not be computed),
    ``reported_value`` (the model's text), ``display_value`` (recomputed value rounded at
    registry precision, None when not reached), ``ok`` and ``reason_code`` (None when ok,
    else the code that rejected this claim).
    """

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
    """The gate's single decision for the whole answer.

    Inputs: built only by ``validate_answer`` (via ``_reject`` or the final pass).
    Holds: ``status`` ("pass" or "reject"); ``reason_code`` (None on pass, else one of
    ``REASON_CODES``); ``message``; ``claim_id`` (the offending claim, None for
    answer-level rejections); ``checked_claims`` (ClaimCheck records up to and including
    the failing claim, or all claims on pass).
    """

    status: Literal["pass", "reject"]
    reason_code: str | None
    message: str
    claim_id: str | None
    checked_claims: tuple[ClaimCheck, ...]


class _Fail(Exception):
    """Internal control-flow exception carrying one stable reason code.

    Args: ``code`` must be a member of ``REASON_CODES`` (asserted, so a typo cannot
    ship); ``message`` is human readable. Raised by every check below and caught only in
    ``evaluate_claim`` (converted to ``EvaluationError``) and ``validate_answer``
    (converted to a reject ``GateVerdict``). Never escapes the module.
    """

    def __init__(self, code: str, message: str) -> None:
        assert code in REASON_CODES, code
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------- rounding

def round_half_up(value: Decimal, precision: int) -> Decimal:
    """Quantize to ``precision`` decimals with ties away from zero (DECISIONS.md #3).

    Args:
        value: a finite ``Decimal`` (the gate's recomputed value or a reported value).
        precision: non-negative number of decimals (registry ``display.precision``).
    Returns:
        ``value`` quantized with ROUND_HALF_UP (0.125 -> 0.13, -0.125 -> -0.13).
    Rejects:
        nothing; the context precision is widened so quantize cannot overflow.
    """
    # Widen precision locally so quantize never raises InvalidOperation on large values.
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION + 10
        return value.quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP)


def _metric(metric: MetricDefinition | str, registry: MetricRegistry) -> MetricDefinition:
    """Resolve a metric given as a definition or an id.

    Args: ``metric`` a ``MetricDefinition`` or registry metric id; ``registry`` the loaded
    ``MetricRegistry``. Returns: the ``MetricDefinition``. Raises ``KeyError`` (from
    ``registry.lookup``) for an unknown id; callers that need a reason code catch it.
    """
    return metric if isinstance(metric, MetricDefinition) else registry.lookup(metric)


def display_precision(metric: MetricDefinition | str, registry: MetricRegistry) -> int:
    """Number of decimals the registry says this metric is displayed with.

    Args: ``metric`` a definition or id; ``registry`` the loaded registry (the source of
    ``display.precision``, never the model). Returns: the precision as a non-negative int.
    Raises: ``ValueError`` when the metric has no valid display precision (a registry
    defect, not a model error); ``KeyError`` for an unknown metric id.
    """
    m = _metric(metric, registry)
    precision = m.display.get("precision")
    if not isinstance(precision, int) or precision < 0:
        raise ValueError(f"metric {m.id!r} has no display precision")
    return precision


def format_display(value: Decimal, metric: MetricDefinition | str, registry: MetricRegistry) -> str:
    """Render ``value`` at the registry display precision with ROUND_HALF_UP.

    Args: ``value`` a finite Decimal; ``metric`` definition or id; ``registry`` the loaded
    registry. Returns: plain decimal text (e.g. "1234.50"). Raises as ``display_precision``.
    """
    return str(round_half_up(value, display_precision(metric, registry)))


# --------------------------------------------------------------------------- cells

def _to_decimal(value: Any, where: str) -> Decimal:
    """Convert one manifest cell to a finite ``Decimal`` without float drift.

    Args:
        value: a manifest cell (``ResultManifest.row_refs[ref][binding_id]``): Decimal,
            int, float, plain decimal text, or something invalid.
        where: "row_ref.binding_id" label used in the message.
    Returns:
        the finite Decimal (floats go through ``repr`` so 0.1 stays 0.1).
    Rejects:
        "null_cell" (None); "non_numeric_cell" (bool, non-decimal text, other types);
        "non_finite_cell" (NaN or infinity).
    """
    # None is "unknown", never zero (fail-closed).
    if value is None:
        raise _Fail("null_cell", f"{where} is null")
    # bool is an int subclass; a flag is not a number.
    if isinstance(value, bool):
        raise _Fail("non_numeric_cell", f"{where} is a boolean")
    # Exact types convert directly; floats via repr to avoid binary-float expansion.
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(str(value))
    elif isinstance(value, float):
        result = Decimal(repr(value))
    # Text cells must be plain decimal text (no exponent, separators or symbols).
    elif isinstance(value, str):
        if not _DECIMAL_TEXT.fullmatch(value.strip()):
            raise _Fail("non_numeric_cell", f"{where} is not plain decimal text")
        result = Decimal(value.strip())
    else:
        raise _Fail("non_numeric_cell", f"{where} has non-numeric type {type(value).__name__}")
    # NaN / Infinity can arise from floats or Decimal inputs; never usable.
    if not result.is_finite():
        raise _Fail("non_finite_cell", f"{where} is not finite")
    return result


def _binding(manifest: ResultManifest, inp: ClaimInput) -> MetricBinding:
    """Find the engine-owned binding a claim input points at.

    Args: ``manifest`` the engine-owned ``ResultManifest`` (``bind_result``); ``inp`` a
    model-supplied ``ClaimInput`` (result_id, binding_id, row_ref, scope).
    Returns: the matching ``MetricBinding`` (kind "metric" or "dimension", ref_id, unit).
    Rejects: "unknown_result_id" (input names another result); "unknown_binding"
    (binding_id not in ``manifest.bindings``).
    """
    if inp.result_id != manifest.result_id:
        raise _Fail("unknown_result_id", f"input names result {inp.result_id!r}")
    for b in manifest.bindings:
        if b.binding_id == inp.binding_id:
            return b
    raise _Fail("unknown_binding", f"binding {inp.binding_id!r} is not in the manifest")


def _check_input_type(inp: Any) -> None:
    """Reject anything in an input position that is not a manifest reference.

    Args: ``inp`` one element of ``ClaimCalculation.inputs`` (model-supplied).
    Returns: None when it is a ``ClaimInput`` or nested ``ClaimCalculation``.
    Rejects: "model_constant" for a literal number, string or any other object, so a
    model can never smuggle in a constant.
    """
    if not isinstance(inp, (ClaimInput, ClaimCalculation)):
        raise _Fail("model_constant", f"input {inp!r} is not a manifest reference")


def _cell(manifest: ResultManifest, inp: ClaimInput, *, numeric: bool = True) -> Any:
    """Read one cell addressed by (row_ref, binding_id).

    Args:
        manifest: engine-owned ``ResultManifest`` (row_refs from ``bind_result``).
        inp: a ``ClaimInput`` with a ``row_ref``.
        numeric: True converts to Decimal and requires a metric binding; False returns
            the raw cell (used for entity text).
    Returns:
        a Decimal (numeric) or the raw cell value.
    Rejects:
        "unknown_result_id", "unknown_binding" (via ``_binding``); "row_ref_required"
        (no row_ref); "unknown_row_ref"; "missing_cell"; "dimension_as_number" (a
        dimension used as a number); plus "null_cell", "non_numeric_cell",
        "non_finite_cell" from ``_to_decimal``.
    """
    # Resolve the binding first so unknown result/binding ids reject before cell access.
    b = _binding(manifest, inp)
    # A single-cell read must name a row the engine produced.
    if inp.row_ref is None:
        raise _Fail("row_ref_required", f"input {inp.binding_id!r} needs a row_ref")
    if inp.row_ref not in manifest.row_refs:
        raise _Fail("unknown_row_ref", f"row ref {inp.row_ref!r} is not in the manifest")
    row = manifest.row_refs[inp.row_ref]
    if inp.binding_id not in row:
        raise _Fail("missing_cell", f"row {inp.row_ref!r} has no {inp.binding_id!r}")
    # Only measure columns are numbers; group labels must not be used arithmetically.
    if numeric and b.kind != "metric":
        raise _Fail("dimension_as_number", f"dimension binding {inp.binding_id!r} used as a number")
    return _to_decimal(row[inp.binding_id], f"{inp.row_ref}.{inp.binding_id}") if numeric else row[inp.binding_id]


def _dimension_ids(manifest: ResultManifest) -> tuple[str, ...]:
    """Binding ids of the result's group-by dimensions.

    Args: ``manifest`` the engine-owned manifest. Returns: tuple of binding ids whose
    ``kind`` is "dimension" (empty for a scalar result). Rejects: nothing.
    """
    return tuple(b.binding_id for b in manifest.bindings if b.kind == "dimension")


def _check_population(manifest: ResultManifest) -> None:
    """Column aggregation needs a complete, detail-only, key-unique population.

    Args: ``manifest`` engine-owned (``completeness`` and ``row_refs`` from
    ``bind_result``). Returns: None when aggregating is safe.
    Rejects: "incomplete_population" (result truncated or otherwise not "complete");
    "subtotal_row_in_population" (a dimension cell is null or a subtotal label such as
    "total"); "duplicate_group_key" (same dimension tuple twice, e.g. a duplicated join).
    """
    # A truncated or unknown-completeness result cannot support a sum/mean/share.
    if manifest.completeness != "complete":
        raise _Fail("incomplete_population", f"result completeness is {manifest.completeness!r}")
    dims = _dimension_ids(manifest)
    # Scalar results have no groups to validate.
    if not dims:
        return
    # Walk every row: reject subtotal/null groups and repeated group keys, because either
    # would make a column aggregate double count or include a non-detail row.
    seen: set[tuple[str, ...]] = set()
    for ref, row in manifest.row_refs.items():
        key = tuple("" if row.get(d) is None else str(row.get(d)) for d in dims)
        if any(row.get(d) is None or str(row.get(d)).strip().lower() in SUBTOTAL_LABELS for d in dims):
            raise _Fail("subtotal_row_in_population", f"row {ref!r} looks like a subtotal/null group")
        if key in seen:
            raise _Fail("duplicate_group_key", f"group key {key!r} repeats (duplicated join?)")
        seen.add(key)


def _column(manifest: ResultManifest, inp: Any, *, numeric: bool = True) -> list[Any]:
    """Read a whole column of the complete population.

    Args:
        manifest: engine-owned ``ResultManifest``.
        inp: must be a ``ClaimInput`` with no ``row_ref`` and
            ``scope == COMPLETE_SCOPE`` (model-supplied).
        numeric: True converts each cell to Decimal and requires a metric binding;
            False returns raw non-null cells (count / distinct_count).
    Returns:
        the column values in manifest row order (never empty).
    Rejects:
        "invalid_operand_shape" (not a whole-column ClaimInput); "missing_scope";
        "invalid_scope"; "incomplete_population", "subtotal_row_in_population",
        "duplicate_group_key" (via ``_check_population``); "dimension_as_number";
        "missing_cell"; "null_cell"; "non_numeric_cell"/"non_finite_cell" (via
        ``_to_decimal``); "empty_population" (no rows); plus "unknown_result_id" and
        "unknown_binding" from ``_binding``.
    """
    # Shape: an aggregate reads a whole column, so a row_ref (single cell) is wrong.
    if not isinstance(inp, ClaimInput) or inp.row_ref is not None:
        raise _Fail("invalid_operand_shape", "an aggregate needs a whole-column input (no row_ref)")
    b = _binding(manifest, inp)
    # Scope: the model must explicitly assert it is aggregating the complete population.
    if inp.scope is None:
        raise _Fail("missing_scope", f"column input {inp.binding_id!r} needs scope {COMPLETE_SCOPE!r}")
    if inp.scope != COMPLETE_SCOPE:
        raise _Fail("invalid_scope", f"scope {inp.scope!r} is not {COMPLETE_SCOPE!r}")
    # The engine verifies the assertion: complete, detail-only, unique group keys.
    _check_population(manifest)
    if numeric and b.kind != "metric":
        raise _Fail("dimension_as_number", f"dimension binding {inp.binding_id!r} used as a number")
    # Collect the column; nulls are undefined, never skipped or treated as zero.
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
    """Nesting depth of a calculation (a flat calculation is 1).

    Args: ``calc`` a model-supplied ``ClaimCalculation``. Returns: int depth.
    Rejects: nothing (callers compare against ``MAX_DEPTH``).
    """
    return 1 + max((_depth(i) for i in calc.inputs if isinstance(i, ClaimCalculation)), default=0)


def _nodes(calc: ClaimCalculation) -> int:
    """Count operation and input nodes in a calculation tree.

    Args: ``calc`` a ``ClaimCalculation``. Returns: int node count (compared against
    ``MAX_NODES`` by ``evaluate_claim``). Rejects: nothing.
    """
    return sum(_nodes(i) if isinstance(i, ClaimCalculation) else 1 for i in calc.inputs) + 1


def _scalar(inp: Any, manifest: ResultManifest, depth: int) -> Decimal:
    """Evaluate one scalar operand: a single cell or a nested calculation.

    Args: ``inp`` a ``ClaimInput`` (with row_ref) or ``ClaimCalculation``; ``manifest``
    engine-owned; ``depth`` current nesting depth. Returns: a Decimal.
    Rejects: "model_constant" (neither type); anything ``_cell`` or ``_eval`` rejects.
    """
    _check_input_type(inp)
    if isinstance(inp, ClaimCalculation):
        return _eval(inp, manifest, depth + 1)
    return _cell(manifest, inp)


def _arity(calc: ClaimCalculation, *allowed: int) -> None:
    """Check the number of inputs an op received.

    Args: ``calc`` the calculation; ``allowed`` the permitted input counts.
    Returns: None. Rejects: "invalid_arity" when ``len(calc.inputs)`` is not allowed.
    """
    if len(calc.inputs) not in allowed:
        raise _Fail("invalid_arity", f"{calc.op} takes {' or '.join(map(str, allowed))} inputs, got {len(calc.inputs)}")


def _div(num: Decimal, den: Decimal) -> Decimal:
    """Divide, treating a zero denominator as undefined rather than 0.

    Args: ``num``, ``den`` Decimals. Returns: ``num / den``.
    Rejects: "undefined_value" when ``den == 0``.
    """
    if den == 0:
        raise _Fail("undefined_value", "zero denominator: the value is undefined, not 0")
    return num / den


def _eval(calc: ClaimCalculation, manifest: ResultManifest, depth: int) -> Decimal:
    """Evaluate a typed calculation with Decimal from manifest cells only.

    Args:
        calc: model-proposed ``ClaimCalculation`` (``op`` plus ``inputs``); the op must
            be one of the typed operations, inputs must be manifest references.
        manifest: engine-owned ``ResultManifest`` (cells, bindings, completeness).
        depth: current nesting depth (1 for the claim's top calculation).
    Returns:
        the exact Decimal result (not yet rounded); no model number is ever used.
    Rejects:
        "depth_exceeded", "model_constant", "invalid_arity", "undefined_value" (zero
        denominator or zero percentage-change baseline), "invalid_operand_shape",
        "operation_not_allowed" (unknown op), and everything ``_cell`` / ``_column``
        reject (cell, scope, population and numeric-cell codes).
    """
    # Guard recursion depth and make sure every input is a manifest reference.
    if depth > MAX_DEPTH:
        raise _Fail("depth_exceeded", f"calculation depth exceeds {MAX_DEPTH}")
    for inp in calc.inputs:
        _check_input_type(inp)
    op = calc.op
    # identity: return exactly one cell (or nested value) unchanged.
    if op == "identity":
        _arity(calc, 1)
        return _scalar(calc.inputs[0], manifest, depth)
    # Binary ops over two scalar operands: add, subtract, multiply, safe_divide and
    # percentage_change = (a - b) / b * 100 where b is the baseline.
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
    # Column aggregates over the complete population: count/distinct_count read raw
    # non-null cells; sum/mean/min/max read Decimals. The column is never empty here.
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
    # weighted_mean: sum(value * weight) / sum(weights); a zero weight total is undefined.
    if op == "weighted_mean":
        _arity(calc, 2)
        values = _column(manifest, calc.inputs[0])
        weights = _column(manifest, calc.inputs[1])
        return _div(sum((v * w for v, w in zip(values, weights)), Decimal(0)), sum(weights, Decimal(0)))
    # share: part / whole. The whole is the full complete-population column of the same
    # binding (one-input form) or an explicit whole-column / scalar denominator.
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
    # Anything else is not in the typed vocabulary.
    raise _Fail("operation_not_allowed", f"operation {op!r} is not supported")


def evaluate_claim(claim: Claim, manifest: ResultManifest,
                   registry: MetricRegistry) -> Decimal | EvaluationError:
    """Recompute ``claim`` from manifest cells only. ``registry`` is unused by arithmetic
    but kept in the signature so registry-owned constants can be added later.

    Args:
        claim: a parsed ``Claim`` (model-proposed calculation; its reported_value is NOT
            read here).
        manifest: engine-owned ``ResultManifest``.
        registry: the ``MetricRegistry`` (currently unused, deleted on entry).
    Returns:
        the recomputed ``Decimal`` (28 significant digits, not rounded) or an
        ``EvaluationError`` carrying a reason code. Never raises for expected failures.
    Rejects (as EvaluationError):
        "calculation_too_large", "depth_exceeded" (also on RecursionError),
        "undefined_value" (also on Decimal InvalidOperation/DivisionByZero), "non_finite_cell",
        and every code ``_eval`` can raise.
    """
    del registry
    try:
        # Size guard first so a huge tree is never evaluated.
        if _nodes(claim.calculation) > MAX_NODES:
            raise _Fail("calculation_too_large", f"calculation has more than {MAX_NODES} nodes")
        # Fixed-precision local Decimal context; invalid operations and division by
        # zero raise instead of silently producing NaN/Infinity.
        with localcontext() as ctx:
            ctx.prec = DECIMAL_PRECISION
            ctx.traps[InvalidOperation] = True
            ctx.traps[DivisionByZero] = True
            value = _eval(claim.calculation, manifest, 1)
        if not value.is_finite():  # pragma: no cover - traps already raise
            raise _Fail("non_finite_cell", "result is not finite")
        return value
    # Convert every expected failure to a value so callers branch, not catch.
    except _Fail as exc:
        return EvaluationError(exc.code, exc.message)
    except (InvalidOperation, DivisionByZero) as exc:
        return EvaluationError("undefined_value", f"arithmetic is undefined: {exc!r}")
    except RecursionError:
        return EvaluationError("depth_exceeded", "calculation is too deep")


# --------------------------------------------------------------------------- compatibility

def _input_bindings(calc: ClaimCalculation, manifest: ResultManifest) -> list[MetricBinding]:
    """Collect the manifest bindings every input (including nested ones) points at.

    Args: ``calc`` the model's calculation; ``manifest`` engine-owned. Returns: list of
    ``MetricBinding`` in input order (nested calculations flattened).
    Rejects: "model_constant"; "unknown_result_id"; "unknown_binding".
    """
    found: list[MetricBinding] = []
    for inp in calc.inputs:
        _check_input_type(inp)
        if isinstance(inp, ClaimCalculation):
            found.extend(_input_bindings(inp, manifest))
        else:
            found.append(_binding(manifest, inp))
    return found


def _unit(inp: Any, manifest: ResultManifest, registry: MetricRegistry) -> str | None:
    """Unit of one operand.

    Args: ``inp`` a ``ClaimInput`` or ``ClaimCalculation``; ``manifest`` engine-owned;
    ``registry`` the metric registry (unit of the bound metric). Returns: the unit
    string, or None for a dimension binding. Rejects: as ``_binding`` / ``_calc_unit``;
    a binding whose ref_id is not registered raises ``KeyError`` (registry defect).
    """
    if isinstance(inp, ClaimCalculation):
        return _calc_unit(inp, manifest, registry)
    b = _binding(manifest, inp)
    if b.kind != "metric":
        return None
    return b.unit or registry.lookup(b.ref_id).unit


def _calc_unit(calc: ClaimCalculation, manifest: ResultManifest, registry: MetricRegistry) -> str:
    """Unit a calculation produces, derived from its operands and op.

    Args: ``calc`` the model's calculation; ``manifest`` engine-owned; ``registry`` the
    metric registry. Returns: the produced unit ("count" for counts, "fraction" for
    safe_divide/share, "percent" for percentage_change, else the operands' unit).
    Rejects: "operation_not_allowed" (multiply: no registry-owned constant can be named);
    "dimension_as_number" (a dimension operand); "unit_mismatch" (operands of
    SAME_UNIT_OPS mix units).
    """
    op = calc.op
    units = [_unit(i, manifest, registry) for i in calc.inputs]
    # Ops with a fixed output unit regardless of operand units.
    if op in ("count", "distinct_count"):
        return "count"
    if op in ("safe_divide", "share"):
        return "fraction"
    if op == "multiply":
        raise _Fail("operation_not_allowed", "multiply needs a registry-owned constant; none can be named")
    # Value operands (a weighted_mean's weights may have a different unit).
    value_units = units[:1] if op == "weighted_mean" else units
    if any(u is None for u in value_units):
        raise _Fail("dimension_as_number", f"{op} has a dimension operand")
    if op in SAME_UNIT_OPS and len(set(value_units)) > 1:
        raise _Fail("unit_mismatch", f"{op} mixes units {sorted(set(map(str, value_units)))}")
    if op == "percentage_change":
        return "percent"
    return str(value_units[0])


def _template_matches(metric: MetricDefinition, calc: ClaimCalculation, bindings: list[MetricBinding]) -> bool:
    """The claim metric is a registry shape for this op over these input metrics.

    Args: ``metric`` the claim's registry definition (its typed ``calculation``);
    ``calc`` the model's calculation; ``bindings`` from ``_input_bindings``.
    Returns: True when the registry calculation uses the same op and either only
    parameters, or metric operands equal to the bound metric ids in order; False
    otherwise (including for nested calculations). Rejects: nothing.
    """
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
    """Part and whole must share one binding; the whole is the full complete-population column.

    Args: ``calc`` a model-supplied ``share`` calculation. Returns: None when shaped
    correctly (one cell input, or a cell plus a same-binding whole column with
    ``COMPLETE_SCOPE``). Rejects: "invalid_share_denominator" (part not a single cell,
    whole not a column, different bindings, or whole scope missing/invalid).
    """
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
    """Whether a count / distinct_count legitimately produces the claim metric.

    Args: ``metric`` the claim's registry definition; ``registry`` the registry (to look
    up each bound metric's source column); ``bindings`` from ``_input_bindings``.
    Returns: True when every bound metric is the claim metric itself, or the claim
    metric is a registry count over a column that is each bound metric's source column.
    False otherwise (including unregistered bindings). Rejects: nothing.
    """
    # Direct case: the counted binding is the claim metric itself.
    metric_bindings = [b for b in bindings if b.kind == "metric"]
    if metric_bindings and all(b.ref_id == metric.id for b in metric_bindings):
        return True
    # Registry case: claim metric is a count over the bound metrics' source columns.
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
    """Check (a): the claim's metric, operation and units agree with the bound columns.

    Args:
        claim: model-proposed ``Claim`` (metric_id, calculation).
        manifest: engine-owned ``ResultManifest`` (bindings carry registry metric ids and
            units, set by ``src/semantic_query.bind_result``).
        registry: the ``MetricRegistry`` (source of truth for metrics, allowed
            operations, units).
    Returns:
        None when the claim may legitimately be reported as ``claim.metric_id``.
    Rejects:
        "unknown_metric"; "irrelevant_number" (dimension-only claim, or a row count
        offered for a non-count metric); "invalid_share_denominator"; "unit_mismatch";
        "metric_binding_mismatch" (inputs bind a different metric than claimed);
        "operation_not_allowed" (op not allowed for the metric); plus codes from
        ``_input_bindings`` / ``_calc_unit``.
    """
    calc = claim.calculation
    # The claimed metric must exist in the registry (the model cannot invent metrics).
    try:
        metric = registry.lookup(claim.metric_id)
    except KeyError as exc:
        raise _Fail("unknown_metric", f"metric {claim.metric_id!r} is not registered") from exc
    # A claim built only from group labels is not a measure at all.
    bindings = _input_bindings(calc, manifest)
    if bindings and all(b.kind == "dimension" for b in bindings):
        raise _Fail("irrelevant_number", "a claim over dimension columns only is not a measure")
    op = calc.op
    # Share-specific shape rules, then the count-vs-measure guard.
    if op == "share":
        _check_share_shape(calc)
    if op in ("count", "distinct_count") and metric.unit != "count":
        raise _Fail("irrelevant_number", f"a row count cannot answer {metric.unit} metric {metric.id!r}")
    # Unit the calculation actually produces (rejects mixed units and dimension operands).
    produced = _calc_unit(calc, manifest, registry)
    metric_ids = {b.ref_id for b in bindings if b.kind == "metric"}
    # Decide whether the op is permitted for this metric. Four routes: (1) the claim
    # metric is a registry template for this op; (2) a pure selection (identity/min/max)
    # of the claim metric itself; (3) a count over the right column; (4) the op is listed
    # in the metric's allowed_operations over inputs bound to that same metric.
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
    # The produced unit must equal the metric's unit ("none" templates adopt the operands').
    expected = metric.unit
    if metric.unit == "none":  # template such as difference: unit is the operands' unit
        expected = produced
    if produced != expected:
        raise _Fail("unit_mismatch", f"{op} produces {produced!r} but metric {metric.id!r} is {metric.unit!r}")


def _aggregates_column(calc: ClaimCalculation) -> bool:
    """Whether a calculation (or any nested part) reads a whole column.

    Args: ``calc`` the model's calculation. Returns: True for an aggregating op, a share
    with an implicit or whole-column denominator, or any nested calculation that does.
    Rejects: nothing.
    """
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
    """Check (e): an incomplete result only supports identity claims on single rows.

    Args: ``claim`` the model's claim; ``manifest`` engine-owned (``completeness`` from
    ``bind_result``). Returns: None when allowed.
    Rejects: "incomplete_population" when the result is not "complete" and the claim is
    not a plain identity, or aggregates a column anywhere inside it.
    """
    if manifest.completeness != "complete" and (claim.calculation.op != "identity"
                                                or _aggregates_column(claim.calculation)):
        raise _Fail("incomplete_population",
                    f"result completeness is {manifest.completeness!r}: only identity claims on rows are allowed")


# --------------------------------------------------------------------------- comparison

def _compare(claim: Claim, value: Decimal, registry: MetricRegistry) -> str:
    """Check (c): the model's reported number equals the recomputed one at display precision.

    Args:
        claim: model-proposed ``Claim`` (``reported_value`` plain decimal text,
            ``metric_id``).
        value: the gate's own recomputed Decimal (from ``evaluate_claim``).
        registry: the registry (source of display precision, DECISIONS.md #3).
    Returns:
        the recomputed value rounded at registry precision, as text (the display value).
    Rejects:
        "invalid_reported_value" (not plain decimal text); "imprecise_value" (fewer
        decimals than the registry precision); "value_mismatch" (any difference after
        rounding both sides at registry precision, or at the reported decimals when the
        model gave more).
    """
    # Reported value must be plain decimal text, so no parsing ambiguity or units.
    text = claim.reported_value
    if not isinstance(text, str) or not _DECIMAL_TEXT.fullmatch(text):
        raise _Fail("invalid_reported_value", f"reported_value {text!r} is not plain decimal text")
    precision = display_precision(claim.metric_id, registry)
    display = round_half_up(value, precision)
    reported = Decimal(text)
    decimals = len(text.split(".")[1]) if "." in text else 0
    # A model may not round more coarsely than the registry display precision.
    if decimals < precision:
        raise _Fail("imprecise_value", f"reported {text} has fewer than {precision} decimals")
    # Equal precision: exact match to the display value. More precision: it must be a
    # correct rounding of the recomputed value that also rounds to the display value.
    if decimals == precision:
        ok = reported == display
    else:
        ok = reported == round_half_up(value, decimals) and round_half_up(reported, precision) == display
    if not ok:
        raise _Fail("value_mismatch", f"reported {text} but the recomputed value displays as {display}")
    return str(display)


def _entity_rows(claim: Claim, manifest: ResultManifest, value: Decimal) -> list[str] | None:
    """Row refs the claim's number belongs to, or None when attribution does not apply.

    Args: ``claim`` the model's claim; ``manifest`` engine-owned; ``value`` the
    recomputed Decimal. Returns: ``[row_ref]`` for an identity/share of one cell; for
    min/max every row whose cell equals ``value`` (ties allowed); None otherwise (the
    value is not one group's number). Rejects: a non-numeric min/max cell raises
    "null_cell" / "non_numeric_cell" / "non_finite_cell" via ``_to_decimal``.
    """
    calc = claim.calculation
    first = calc.inputs[0] if calc.inputs else None
    if calc.op in ("identity", "share") and isinstance(first, ClaimInput) and first.row_ref is not None:
        return [first.row_ref]
    if calc.op in ("min", "max") and isinstance(first, ClaimInput):
        return [ref for ref, row in manifest.row_refs.items()
                if _to_decimal(row.get(first.binding_id), ref) == value]
    return None


def _check_entity(claim: Claim, manifest: ResultManifest, value: Decimal) -> None:
    """Check (d): the entity the claim names is the row its number actually comes from.

    Args:
        claim: model-proposed ``Claim`` with ``entity_refs`` mapping dimension binding id
            -> the group value the model says the number belongs to.
        manifest: engine-owned ``ResultManifest`` (dimension cells in row_refs).
        value: the recomputed Decimal.
    Returns:
        None when attribution is correct or not applicable (a scalar result has no
        entities).
    Rejects:
        "missing_entity_ref" (a single-row claim does not name every dimension);
        "entity_mismatch" (named entity differs from the source row, or a column
        aggregate is attributed to a group). Dates and floats are compared as text via
        ``_cell_text``.
    """
    # Scalar results have no entity to attribute.
    dims = _dimension_ids(manifest)
    if not dims:
        return
    rows = _entity_rows(claim, manifest, value)
    # Branch 1: the number is not one group's cell (e.g. a total, a difference). The model
    # may not attribute it to a group; if it names one anyway, every input row must match.
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
    # Branch 2: the number is a group's cell. The claim must name every dimension and the
    # names must equal the source row (any tied row suffices for min/max).
    missing = [d for d in dims if d not in claim.entity_refs]
    if missing:
        raise _Fail("missing_entity_ref", f"claim must name its entity for {missing}")
    for ref in rows:
        row = manifest.row_refs[ref]
        if all(claim.entity_refs[d] == _cell_text(row.get(d)) for d in dims):
            return
    raise _Fail("entity_mismatch", f"entity {dict((d, claim.entity_refs[d]) for d in dims)} is not the row the value comes from")


def _calc_row_refs(calc: ClaimCalculation) -> set[str]:
    """Every row ref a calculation reads as a single cell (nested included).

    Args: ``calc`` the model's calculation. Returns: set of row_ref strings (whole-column
    inputs contribute none). Rejects: nothing.
    """
    out: set[str] = set()
    for inp in calc.inputs:
        if isinstance(inp, ClaimCalculation):
            out |= _calc_row_refs(inp)
        elif isinstance(inp, ClaimInput) and inp.row_ref is not None:
            out.add(inp.row_ref)
    return out


def _cell_text(value: Any) -> str | None:
    """Canonical text of a dimension cell for entity comparison.

    Args: ``value`` a manifest cell. Returns: None for None; ISO text for date-likes;
    exact decimal text for floats; ``str(value)`` otherwise. Rejects: nothing.
    """
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, float):
        return str(Decimal(repr(value)))
    return str(value)


# --------------------------------------------------------------------------- coverage

def _referenced_bindings(calc: ClaimCalculation) -> set[str]:
    """Binding ids a calculation reads (nested included).

    Args: ``calc`` the model's calculation. Returns: set of ``binding_id`` strings used
    by its ``ClaimInput`` leaves. Rejects: nothing.
    """
    out: set[str] = set()
    for inp in calc.inputs:
        if isinstance(inp, ClaimCalculation):
            out |= _referenced_bindings(inp)
        elif isinstance(inp, ClaimInput):
            out.add(inp.binding_id)
    return out


def _referenced_rows(claim: Claim, manifest: ResultManifest) -> set[str]:
    """Rows in calculation inputs only; evidence_refs never count toward coverage.

    Args: ``claim`` the model's claim; ``manifest`` (unused). Returns: set of row refs
    the claim's calculation actually reads. Rejects: nothing.
    """
    del manifest
    return _calc_row_refs(claim.calculation)


def _check_coverage(contract: AnswerContract, manifest: ResultManifest,
                    question_metric_ids: tuple[str, ...] | list[str] | None) -> None:
    """Check (f): positive coverage. The answer must actually answer the question.

    Args:
        contract: the parsed ``AnswerContract`` (claims with ``answer_role``).
        manifest: engine-owned ``ResultManifest`` (bindings, row_refs, completeness,
            row_count).
        question_metric_ids: registry metric ids the QUESTION asked for (from the
            semantic query intent, not the model); empty/None skips that check.
    Returns:
        None when covered.
    Rejects:
        "missing_direct_answer" (no ``direct_answer`` claim, or a metric binding no direct
        claim uses); "scalar_not_identity" (scalar result whose direct claim is not a
        plain identity of the cell); "missing_group_coverage" (complete grouped result of
        at most ``GROUP_COVERAGE_MAX_ROWS`` rows with a row no claim references);
        "question_metric_not_answered".
    """
    # There must be at least one claim flagged as the direct answer.
    direct = [c for c in contract.claims if c.answer_role == "direct_answer"]
    if not direct:
        raise _Fail("missing_direct_answer", "the answer has no direct_answer claim")
    # Every measure the engine returned must be used by some direct answer claim.
    for b in manifest.bindings:
        if b.kind == "metric" and not any(b.binding_id in _referenced_bindings(c.calculation) for c in direct):
            raise _Fail("missing_direct_answer", f"no direct_answer claim uses binding {b.binding_id!r}")
    dims = _dimension_ids(manifest)
    # Scalar result: the answer is the cell itself, with no derived value in between.
    if not dims:
        for c in direct:
            if c.calculation.op != "identity" or any(isinstance(i, ClaimCalculation) for i in c.calculation.inputs):
                raise _Fail("scalar_not_identity", f"claim {c.claim_id!r} must be an identity of the scalar cell")
    # Small complete grouped result: every row must appear in some claim's inputs (an
    # answer may not silently drop groups). evidence_refs do not count.
    elif manifest.completeness == "complete" and manifest.row_count <= GROUP_COVERAGE_MAX_ROWS:
        covered: set[str] = set()
        for c in contract.claims:
            covered |= _referenced_rows(c, manifest)
        missing = sorted(set(manifest.row_refs) - covered)
        if missing:
            raise _Fail("missing_group_coverage", f"rows {missing} are not referenced by any claim")
    # Every metric the question asked for must have a direct answer.
    if question_metric_ids:
        answered = {c.metric_id for c in direct}
        for metric_id in question_metric_ids:
            if metric_id not in answered:
                raise _Fail("question_metric_not_answered", f"question metric {metric_id!r} has no direct answer")


# --------------------------------------------------------------------------- gate

def _reject(code: str, message: str, claim_id: str | None, checks: list[ClaimCheck]) -> GateVerdict:
    """Build a reject verdict.

    Args: ``code`` a ``REASON_CODES`` member; ``message``; ``claim_id`` the offending
    claim or None; ``checks`` the ``ClaimCheck`` records so far. Returns: a
    ``GateVerdict`` with status "reject".
    """
    return GateVerdict("reject", code, message, claim_id, tuple(checks))


def validate_answer(contract: AnswerContract, manifest: ResultManifest, registry: MetricRegistry, *,
                    question_metric_ids: tuple[str, ...] | list[str]) -> GateVerdict:
    """Pass only if every claim recomputes, is attributed correctly and the answer covers it.

    Args:
        contract: the parsed, model-proposed ``AnswerContract`` (request_id, result_id,
            claims, limitations) from ``src/analysis_contracts.py``. Untrusted.
        manifest: the engine-owned ``ResultManifest`` from
            ``src/semantic_query.bind_result`` (result_id, request_id, bindings, row_refs,
            completeness, row_count). Trusted.
        registry: the loaded ``MetricRegistry`` (metrics, units, allowed operations,
            display precision). Trusted.
        question_metric_ids: metric ids the question's query intent asked for (from
            ``src/semantic_query``), keyword-only; every one needs a direct answer.
    Returns:
        a ``GateVerdict``: status "pass" with every claim's ``ClaimCheck``, or "reject"
        with ONE reason code, the offending claim id when claim-level, and the checks so far.
        The whole answer is rejected on the first failure; nothing partial passes.
    Rejects (reason codes):
        answer level: "result_id_mismatch", "too_many_claims", "duplicate_claim_id",
        "missing_population_limitation", and the coverage codes "missing_direct_answer",
        "scalar_not_identity", "missing_group_coverage", "question_metric_not_answered".
        claim level: everything from compatibility ("unknown_metric", "irrelevant_number",
        "metric_binding_mismatch", "operation_not_allowed", "unit_mismatch",
        "invalid_share_denominator"), scope ("incomplete_population"), evaluation (cell,
        scope, population, arity, "undefined_value", "depth_exceeded",
        "calculation_too_large", "model_constant", ...), comparison ("invalid_reported_value",
        "imprecise_value", "value_mismatch") and entity ("missing_entity_ref",
        "entity_mismatch") checks.
    """
    checks: list[ClaimCheck] = []
    # Block 1 - binding and size: the contract must belong to this exact result/request
    # (both ids are engine-owned) and stay within the claim budget.
    if contract.result_id != manifest.result_id or contract.request_id != manifest.request_id:
        return _reject("result_id_mismatch", "contract is not bound to this manifest", None, checks)
    if len(contract.claims) > MAX_CLAIMS:
        return _reject("too_many_claims", f"more than {MAX_CLAIMS} claims", None, checks)
    # Block 2 - uniqueness: claim ids must be unique so audit records are unambiguous.
    seen: set[str] = set()
    for claim in contract.claims:
        if claim.claim_id in seen:
            return _reject("duplicate_claim_id", f"claim id {claim.claim_id!r} repeats", claim.claim_id, checks)
        seen.add(claim.claim_id)

    # Block 3 - per-claim pipeline. Stops at the first failing claim and rejects the whole
    # answer. recomputed/display start as None so a failing ClaimCheck records how far the
    # claim got before it failed.
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
        # Claim passed all four checks: record the audit entry and move on.
        checks.append(ClaimCheck(claim.claim_id, claim.metric_id, claim.calculation.op, str(recomputed),
                                 claim.reported_value, display, True, None))

    # Block 4 - answer-level checks after every claim verified: an incomplete result must
    # declare the "population_incomplete" limitation, then positive coverage (f).
    try:
        if manifest.completeness != "complete" and INCOMPLETE_LIMITATION not in contract.limitations:
            raise _Fail("missing_population_limitation",
                        f"an incomplete result needs the {INCOMPLETE_LIMITATION!r} limitation")
        _check_coverage(contract, manifest, question_metric_ids)    # (f)
    except _Fail as exc:
        return _reject(exc.code, exc.message, None, checks)
    # Block 5 - everything verified.
    return GateVerdict("pass", None, f"{len(checks)} claims verified", None, tuple(checks))
