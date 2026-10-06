"""Typed analysis-model answer contract and its strict reference parser (Phase 3).

The analysis model proposes an ``AnswerContract`` as JSON. ``parse_answer_contract``
checks the structure and that every identifier it uses (request, result, question,
metrics, bindings, row refs, evidence refs, operations, template ids) resolves
against the engine-owned ``ResultManifest`` and the registry. It does not evaluate
any arithmetic: recomputing claim values is the Phase 4 calculation gate.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from src.semantic_query import ResultManifest
from src.semantics.registry import OPERATIONS, MetricRegistry

ANSWER_CONTRACT_SCHEMA_VERSION = "1"
ANSWER_ROLES = frozenset({"direct_answer", "comparison", "supporting"})
FORMAT_IDS = frozenset({"money_2dp", "fraction_4dp", "percent_1dp", "count_0dp", "points_0dp"})

_TOP_FIELDS = frozenset({"schema_version", "request_id", "result_id", "question", "received_metric_ids",
                         "answer_metric_ids", "interpretation", "claims", "limitations", "follow_up"})
_INTERP_FIELDS = frozenset({"population", "filters", "dimensions", "grain", "weighting", "denominator"})
_CLAIM_FIELDS = frozenset({"claim_id", "metric_id", "answer_role", "calculation", "reported_value",
                           "entity_refs", "evidence_refs", "format_id"})
_CALC_FIELDS = frozenset({"op", "inputs"})
_INPUT_REQUIRED = frozenset({"result_id", "binding_id"})
_INPUT_OPTIONAL = frozenset({"row_ref", "scope"})
_DECIMAL_TEXT = re.compile(r"-?[0-9]+(\.[0-9]+)?")
_TEMPLATE_ID = re.compile(r"^[a-z][a-z_]*$")
_ENTITY_KEY_POPULATION = "population_id"


@dataclass(frozen=True)
class Interpretation:
    population: str
    filters: tuple[str, ...]
    dimensions: tuple[str, ...]
    grain: str | None
    weighting: str | None
    denominator: str | None


@dataclass(frozen=True)
class ClaimInput:
    result_id: str
    binding_id: str
    row_ref: str | None = None
    scope: str | None = None


@dataclass(frozen=True)
class ClaimCalculation:
    op: str
    inputs: tuple[ClaimInput, ...]


@dataclass(frozen=True)
class Claim:
    claim_id: str
    metric_id: str
    answer_role: str
    calculation: ClaimCalculation
    reported_value: str
    entity_refs: dict[str, str]
    evidence_refs: tuple[str, ...]
    format_id: str


@dataclass(frozen=True)
class AnswerContract:
    schema_version: str
    request_id: str
    result_id: str
    question: str
    received_metric_ids: tuple[str, ...]
    answer_metric_ids: tuple[str, ...]
    interpretation: Interpretation
    claims: tuple[Claim, ...]
    limitations: tuple[str, ...]
    follow_up: tuple[str, ...]


@dataclass(frozen=True)
class ContractRejection:
    reason_code: str
    message: str
    claim_id: str | None = None


class _Reject(Exception):
    def __init__(self, code: str, message: str, claim_id: str | None = None) -> None:
        super().__init__(message)
        self.rejection = ContractRejection(code, message, claim_id)


def _exact_keys(obj: Any, required: frozenset[str], where: str, claim_id: str | None = None,
                optional: frozenset[str] = frozenset()) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise _Reject("invalid_structure", f"{where} must be an object", claim_id)
    keys = set(obj)
    if required - keys:
        raise _Reject("missing_fields", f"{where} is missing {sorted(required - keys)}", claim_id)
    if keys - required - optional:
        raise _Reject("unknown_fields", f"{where} has unknown fields {sorted(keys - required - optional)}", claim_id)
    return obj


def _str(value: Any, where: str, claim_id: str | None = None, *, nullable: bool = False) -> Any:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise _Reject("invalid_field_type", f"{where} must be a string", claim_id)
    return value


def _str_list(value: Any, where: str, claim_id: str | None = None) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _Reject("invalid_field_type", f"{where} must be a list of strings", claim_id)
    return tuple(value)


def _template_ids(value: Any, where: str) -> tuple[str, ...]:
    items = _str_list(value, where)
    for item in items:
        if any(ch.isdigit() for ch in item):
            raise _Reject("numeric_in_template_ref", f"{where} entry {item!r} contains a digit")
        if not _TEMPLATE_ID.match(item):
            raise _Reject("invalid_template_id", f"{where} entry {item!r} is not a template id")
    return items


def manifest_for_prompt(manifest: ResultManifest) -> dict[str, Any]:
    """The minimum the analysis model needs; values are rendered as text, never floats."""
    return {
        "request_id": manifest.request_id,
        "result_id": manifest.result_id,
        "question": manifest.question,
        "bindings": [{"id": b.binding_id, "kind": b.kind, "metric": b.ref_id if b.kind == "metric" else None,
                      "dimension": b.ref_id if b.kind == "dimension" else None,
                      "unit": b.unit, "currency": b.currency, "grain": b.grain} for b in manifest.bindings],
        "row_refs": {ref: {k: _cell_text(v) for k, v in row.items()} for ref, row in manifest.row_refs.items()},
        "completeness": manifest.completeness,
    }


def _cell_text(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, float):  # a float cell is rendered via repr-free Decimal text
        return str(Decimal(repr(value)))
    return str(value)


def parse_answer_contract(text: str, *, manifest: ResultManifest,
                          registry: MetricRegistry) -> AnswerContract | ContractRejection:
    """Strictly parse and reference-check the model's answer contract."""
    try:
        return _parse(text, manifest, registry)
    except _Reject as exc:
        return exc.rejection


def _parse(text: str, manifest: ResultManifest, registry: MetricRegistry) -> AnswerContract:
    try:
        raw = json.loads(text, parse_constant=_reject_constant)
    except _Reject:
        raise
    except (TypeError, ValueError) as exc:
        raise _Reject("invalid_json", f"contract is not valid JSON: {exc}") from exc
    top = _exact_keys(raw, _TOP_FIELDS, "contract")

    if top["schema_version"] != ANSWER_CONTRACT_SCHEMA_VERSION:
        raise _Reject("schema_version_mismatch", f"schema_version must be {ANSWER_CONTRACT_SCHEMA_VERSION!r}")
    for name, expected in (("request_id", manifest.request_id), ("result_id", manifest.result_id),
                           ("question", manifest.question)):
        if top[name] != expected:
            raise _Reject(f"{name}_mismatch", f"{name} does not match the engine-issued value")

    registry_ids = {m.id for m in registry.metrics}
    received = _str_list(top["received_metric_ids"], "received_metric_ids")
    if sorted(received) != sorted(manifest.metric_ids):
        raise _Reject("received_metrics_mismatch", "received_metric_ids must equal the manifest metric bindings")
    answer_ids = _str_list(top["answer_metric_ids"], "answer_metric_ids")
    for metric_id in answer_ids:
        if metric_id not in registry_ids:
            raise _Reject("unknown_metric", f"answer metric {metric_id!r} is not registered")
        if metric_id not in received:
            raise _Reject("answer_metric_not_received", f"answer metric {metric_id!r} was not supplied")

    interp_raw = _exact_keys(top["interpretation"], _INTERP_FIELDS, "interpretation")
    interpretation = Interpretation(
        population=_str(interp_raw["population"], "interpretation.population"),
        filters=_str_list(interp_raw["filters"], "interpretation.filters"),
        dimensions=_str_list(interp_raw["dimensions"], "interpretation.dimensions"),
        grain=_str(interp_raw["grain"], "interpretation.grain", nullable=True),
        weighting=_str(interp_raw["weighting"], "interpretation.weighting", nullable=True),
        denominator=_str(interp_raw["denominator"], "interpretation.denominator", nullable=True),
    )
    manifest_dims = {b.ref_id for b in manifest.bindings if b.kind == "dimension"}
    for dim in interpretation.dimensions:
        if dim not in manifest_dims:
            raise _Reject("interpretation_dimension_mismatch", f"dimension {dim!r} is not in the result")

    if not isinstance(top["claims"], list):
        raise _Reject("invalid_field_type", "claims must be a list")
    claims = tuple(_parse_claim(c, manifest, registry_ids, received) for c in top["claims"])
    seen: set[str] = set()
    for claim in claims:
        if claim.claim_id in seen:
            raise _Reject("duplicate_claim_id", f"claim id {claim.claim_id!r} repeats", claim.claim_id)
        seen.add(claim.claim_id)
    if not claims and manifest.metric_ids and manifest.row_count >= 1:
        raise _Reject("empty_claims", "an answerable result needs at least one claim")

    if manifest.row_count >= 1 and (not answer_ids or not any(c.answer_role == "direct_answer" for c in claims)):
        raise _Reject("no_direct_answer", "a non-empty result needs answer_metric_ids and a direct_answer claim")

    return AnswerContract(
        schema_version=top["schema_version"], request_id=top["request_id"], result_id=top["result_id"],
        question=top["question"], received_metric_ids=received, answer_metric_ids=answer_ids,
        interpretation=interpretation, claims=claims,
        limitations=_template_ids(top["limitations"], "limitations"),
        follow_up=_template_ids(top["follow_up"], "follow_up"),
    )


def _reject_constant(name: str) -> Any:
    raise _Reject("invalid_json", f"non-finite JSON constant {name} is not allowed")


def _parse_claim(raw: Any, manifest: ResultManifest, registry_ids: set[str],
                 received: tuple[str, ...]) -> Claim:
    claim_id = raw.get("claim_id") if isinstance(raw, dict) else None
    claim_id = claim_id if isinstance(claim_id, str) else None
    obj = _exact_keys(raw, _CLAIM_FIELDS, "claim", claim_id)
    claim_id = _str(obj["claim_id"], "claim_id", claim_id)
    if not claim_id:
        raise _Reject("invalid_field_type", "claim_id must be non-empty")
    metric_id = _str(obj["metric_id"], "metric_id", claim_id)
    if metric_id not in registry_ids:
        raise _Reject("unknown_metric", f"claim metric {metric_id!r} is not registered", claim_id)
    if metric_id not in received:
        raise _Reject("claim_metric_not_received", f"claim metric {metric_id!r} was not supplied", claim_id)
    if obj["answer_role"] not in ANSWER_ROLES:
        raise _Reject("invalid_answer_role", f"answer_role must be one of {sorted(ANSWER_ROLES)}", claim_id)
    if obj["format_id"] not in FORMAT_IDS:
        raise _Reject("invalid_format_id", f"format_id must be one of {sorted(FORMAT_IDS)}", claim_id)

    value = obj["reported_value"]
    if not isinstance(value, str) or not _DECIMAL_TEXT.fullmatch(value):
        raise _Reject("invalid_reported_value", "reported_value must be plain decimal text", claim_id)
    try:
        if not Decimal(value).is_finite():  # pragma: no cover - regex already excludes
            raise InvalidOperation
    except InvalidOperation as exc:
        raise _Reject("invalid_reported_value", "reported_value is not a finite decimal", claim_id) from exc

    binding_ids = {b.binding_id for b in manifest.bindings}
    calc = _exact_keys(obj["calculation"], _CALC_FIELDS, "calculation", claim_id)
    if calc["op"] not in OPERATIONS:
        raise _Reject("unknown_operation", f"operation {calc['op']!r} is not allowlisted", claim_id)
    if not isinstance(calc["inputs"], list) or not calc["inputs"]:
        raise _Reject("invalid_field_type", "calculation.inputs must be a non-empty list", claim_id)
    inputs: list[ClaimInput] = []
    for item in calc["inputs"]:
        inp = _exact_keys(item, _INPUT_REQUIRED, "calculation input", claim_id, optional=_INPUT_OPTIONAL)
        if inp["result_id"] != manifest.result_id:
            raise _Reject("unknown_result_id", "calculation input names another result", claim_id)
        if inp["binding_id"] not in binding_ids:
            raise _Reject("unknown_binding", f"binding {inp['binding_id']!r} is not in the manifest", claim_id)
        row_ref = _str(inp.get("row_ref"), "row_ref", claim_id, nullable=True)
        if row_ref is not None and row_ref not in manifest.row_refs:
            raise _Reject("unknown_row_ref", f"row ref {row_ref!r} is not in the manifest", claim_id)
        inputs.append(ClaimInput(inp["result_id"], inp["binding_id"], row_ref,
                                 _str(inp.get("scope"), "scope", claim_id, nullable=True)))

    if calc["op"] == "identity" and len(inputs) == 1:
        bound = next(b for b in manifest.bindings if b.binding_id == inputs[0].binding_id)
        if bound.ref_id != metric_id:
            raise _Reject("claim_metric_binding_mismatch",
                          f"identity input binds {bound.ref_id!r}, not claim metric {metric_id!r}", claim_id)

    entity_refs = obj["entity_refs"]
    if not isinstance(entity_refs, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                    for k, v in entity_refs.items()):
        raise _Reject("invalid_field_type", "entity_refs must map strings to strings", claim_id)
    dimension_bindings = {b.binding_id for b in manifest.bindings if b.kind == "dimension"}
    for key, ref in entity_refs.items():
        if key == _ENTITY_KEY_POPULATION:
            continue
        if key not in dimension_bindings:
            raise _Reject("unknown_entity_ref", f"entity key {key!r} is not a dimension binding", claim_id)
        values = {_cell_text(row.get(key)) for row in manifest.row_refs.values()}
        if ref not in values:
            raise _Reject("unknown_entity_ref", f"entity {ref!r} is not a value of {key!r} in the result", claim_id)

    evidence = _str_list(obj["evidence_refs"], "evidence_refs", claim_id)
    for ref in evidence:
        if not _evidence_resolves(ref, manifest, binding_ids):
            raise _Reject("unknown_evidence_ref", f"evidence ref {ref!r} does not resolve", claim_id)

    return Claim(claim_id, metric_id, obj["answer_role"], ClaimCalculation(calc["op"], tuple(inputs)),
                 value, dict(entity_refs), evidence, obj["format_id"])


def _evidence_resolves(ref: str, manifest: ResultManifest, binding_ids: set[str]) -> bool:
    """Evidence refs are ``result_id:binding_id`` (a column) or ``result_id:row_ref:binding_id`` (a cell)."""
    prefix = manifest.result_id + ":"
    if not ref.startswith(prefix):
        return False
    parts = ref[len(prefix):].split(":")
    if len(parts) == 1:
        return parts[0] in binding_ids
    if len(parts) == 2:
        return parts[0] in manifest.row_refs and parts[1] in binding_ids
    return False
