"""Hermetic tests for the analysis-model answer contract parser (Phase 3: references only)."""

from __future__ import annotations

import copy
import json
from decimal import Decimal
from typing import Any

import pytest

from src.analysis_contracts import (
    ANSWER_CONTRACT_SCHEMA_VERSION,
    AnswerContract,
    ContractRejection,
    manifest_for_prompt,
    parse_answer_contract,
)
from src.semantic_query import CompiledQuery, QueryIntent, bind_result, compile_query, parse_query_intent
from src.semantics.registry import MetricRegistry, load_registry

QUESTION = "Which product category generated the most profit in 2025?"


@pytest.fixture(scope="module")
def registry() -> MetricRegistry:
    return load_registry()


@pytest.fixture(scope="module")
def manifest(registry):
    intent = parse_query_intent(json.dumps({
        "metric_id": "total_profit", "dimensions": ["category"],
        "filters": [{"dimension_id": "sale_date", "op": "between", "values": ["2025-01-01", "2025-12-31"]}],
        "date_grain": None, "order": [], "limit": None, "request_id": "req-1"}), registry)
    assert isinstance(intent, QueryIntent)
    compiled = compile_query(intent, registry, project_id="p", dataset_id="d")
    assert isinstance(compiled, CompiledQuery)
    rows = [{"category": "Electronics", "total_profit": Decimal("5000.10")},
            {"category": "Toys", "total_profit": Decimal("1200.00")}]
    return bind_result(compiled, {"status": "success", "executed_sql": compiled.sql, "run_id": "run-1", "job_id": "job-1", "rows": rows,
                                  "row_count": 2, "total_result_rows": 2, "result_truncated_by_client": False},
                       request_id="req-1", question=QUESTION, registry_version=registry.registry_version)


def valid_contract(manifest) -> dict[str, Any]:
    rid = manifest.result_id
    return {
        "schema_version": ANSWER_CONTRACT_SCHEMA_VERSION,
        "request_id": "req-1",
        "result_id": rid,
        "question": QUESTION,
        "received_metric_ids": ["total_profit"],
        "answer_metric_ids": ["total_profit"],
        "interpretation": {"population": "fact_sales lines in 2025", "filters": ["sale_date in 2025"],
                           "dimensions": ["category"], "grain": "category", "weighting": None, "denominator": None},
        "claims": [{
            "claim_id": "c1", "metric_id": "total_profit", "answer_role": "direct_answer",
            "calculation": {"op": "max", "inputs": [{"result_id": rid, "binding_id": "total_profit",
                                                     "scope": "complete_group_population"}]},
            "reported_value": "5000.10",
            "entity_refs": {"category": "Electronics"},
            "evidence_refs": [f"{rid}:r0:total_profit", f"{rid}:total_profit"],
            "format_id": "money_2dp",
        }],
        "limitations": ["membership_is_current_attribute"],
        "follow_up": ["compare_previous_year"],
    }


def parse(body: dict[str, Any], manifest, registry):
    return parse_answer_contract(json.dumps(body), manifest=manifest, registry=registry)


def test_accepts_valid_contract(manifest, registry):
    result = parse(valid_contract(manifest), manifest, registry)
    assert isinstance(result, AnswerContract), result
    claim = result.claims[0]
    assert claim.calculation.op == "max"
    assert claim.calculation.inputs[0].binding_id == "total_profit"
    assert Decimal(claim.reported_value) == Decimal("5000.10")


def test_row_ref_input_accepted(manifest, registry):
    body = valid_contract(manifest)
    body["claims"][0]["calculation"] = {"op": "identity", "inputs": [
        {"result_id": manifest.result_id, "binding_id": "total_profit", "row_ref": "r0"}]}
    assert isinstance(parse(body, manifest, registry), AnswerContract)


def test_identity_binding_must_match_claim_metric(manifest, registry):
    body = valid_contract(manifest)
    body["claims"][0]["calculation"] = {"op": "identity", "inputs": [
        {"result_id": manifest.result_id, "binding_id": "category", "row_ref": "r0"}]}
    result = parse(body, manifest, registry)
    assert isinstance(result, ContractRejection) and result.reason_code == "claim_metric_binding_mismatch"


def mutate(path: list[Any], value: Any = None, *, delete: bool = False):
    def apply(body: dict[str, Any]) -> dict[str, Any]:
        body = copy.deepcopy(body)
        target = body
        for key in path[:-1]:
            target = target[key]
        if delete:
            del target[path[-1]]
        else:
            target[path[-1]] = value
        return body
    return apply


CLAIM = ["claims", 0]
REJECTIONS = [
    ("missing top field", mutate(["follow_up"], delete=True), "missing_fields"),
    ("extra top field", mutate(["confidence"], 0.9), "unknown_fields"),
    ("schema version", mutate(["schema_version"], "2"), "schema_version_mismatch"),
    ("request id", mutate(["request_id"], "req-2"), "request_id_mismatch"),
    ("result id", mutate(["result_id"], "result-invented"), "result_id_mismatch"),
    ("question rewritten", mutate(["question"], QUESTION + " "), "question_mismatch"),
    ("received inventory", mutate(["received_metric_ids"], ["total_profit", "total_net_sales"]),
     "received_metrics_mismatch"),
    ("answer metric not received", mutate(["answer_metric_ids"], ["total_net_sales"]),
     "answer_metric_not_received"),
    ("answer metric unregistered", mutate(["answer_metric_ids"], ["average_price"]), "unknown_metric"),
    ("interpretation extra", mutate(["interpretation", "confidence"], "high"), "unknown_fields"),
    ("interpretation dimension", mutate(["interpretation", "dimensions"], ["brand"]),
     "interpretation_dimension_mismatch"),
    ("empty claims", mutate(["claims"], []), "empty_claims"),
    ("claim extra field", mutate([*CLAIM, "note"], "x"), "unknown_fields"),
    ("claim missing field", mutate([*CLAIM, "format_id"], delete=True), "missing_fields"),
    ("claim metric unregistered", mutate([*CLAIM, "metric_id"], "average_price"), "unknown_metric"),
    ("answer role", mutate([*CLAIM, "answer_role"], "headline"), "invalid_answer_role"),
    ("format id", mutate([*CLAIM, "format_id"], "money_9dp"), "invalid_format_id"),
    ("unknown op", mutate([*CLAIM, "calculation", "op"], "eval"), "unknown_operation"),
    ("calc extra", mutate([*CLAIM, "calculation", "sql"], "SELECT 1"), "unknown_fields"),
    ("empty inputs", mutate([*CLAIM, "calculation", "inputs"], []), "invalid_field_type"),
    ("unknown binding", mutate([*CLAIM, "calculation", "inputs", 0, "binding_id"], "average_price"),
     "unknown_binding"),
    ("other result", mutate([*CLAIM, "calculation", "inputs", 0, "result_id"], "result-x"), "unknown_result_id"),
    ("unknown row ref", mutate([*CLAIM, "calculation", "inputs", 0, "row_ref"], "r9"), "unknown_row_ref"),
    ("input extra field", mutate([*CLAIM, "calculation", "inputs", 0, "value"], "1"), "unknown_fields"),
    ("unknown evidence", mutate([*CLAIM, "evidence_refs"], ["result-x:r0:total_profit"]), "unknown_evidence_ref"),
    ("evidence row", mutate([*CLAIM, "evidence_refs"], ["{rid}:r7:total_profit"]), "unknown_evidence_ref"),
    ("entity value", mutate([*CLAIM, "entity_refs"], {"category": "Garden"}), "unknown_entity_ref"),
    ("entity key", mutate([*CLAIM, "entity_refs"], {"planet": "Mars"}), "unknown_entity_ref"),
    ("value scientific", mutate([*CLAIM, "reported_value"], "5.0001E3"), "invalid_reported_value"),
    ("value NaN", mutate([*CLAIM, "reported_value"], "NaN"), "invalid_reported_value"),
    ("value Infinity", mutate([*CLAIM, "reported_value"], "Infinity"), "invalid_reported_value"),
    ("value number", mutate([*CLAIM, "reported_value"], 5000.1), "invalid_reported_value"),
    ("value with comma", mutate([*CLAIM, "reported_value"], "5,000.10"), "invalid_reported_value"),
    ("limitation digits", mutate(["limitations"], ["profit_was_9000"]), "numeric_in_template_ref"),
    ("follow-up digits", mutate(["follow_up"], ["compare_2024"]), "numeric_in_template_ref"),
    ("claim metric not received", mutate([*CLAIM, "metric_id"], "total_net_sales"), "claim_metric_not_received"),
    ("no direct answer role", mutate([*CLAIM, "answer_role"], "supporting"), "no_direct_answer"),
    ("no answer metric ids", mutate(["answer_metric_ids"], []), "no_direct_answer"),
    ("value trailing newline", mutate([*CLAIM, "reported_value"], "5000.10\n"), "invalid_reported_value"),
    ("value non-ascii digits", mutate([*CLAIM, "reported_value"], "５０００"), "invalid_reported_value"),
    ("follow-up prose", mutate(["follow_up"], ["Compare with last year"]), "invalid_template_id"),
]


@pytest.mark.parametrize("label, change, code", REJECTIONS, ids=[r[0] for r in REJECTIONS])
def test_rejections(manifest, registry, label, change, code):
    body = change(valid_contract(manifest))
    if label == "evidence row":
        body["claims"][0]["evidence_refs"] = [f"{manifest.result_id}:r7:total_profit"]
    result = parse(body, manifest, registry)
    assert isinstance(result, ContractRejection), label
    assert result.reason_code == code, (label, result)


def test_duplicate_claim_ids(manifest, registry):
    body = valid_contract(manifest)
    body["claims"].append(copy.deepcopy(body["claims"][0]))
    result = parse(body, manifest, registry)
    assert isinstance(result, ContractRejection)
    assert (result.reason_code, result.claim_id) == ("duplicate_claim_id", "c1")


def test_invalid_json_and_non_finite_constants(manifest, registry):
    text = json.dumps(valid_contract(manifest)).replace('"reported_value": "5000.10"', '"reported_value": NaN')
    for raw, code in (("{nope", "invalid_json"), (text, "invalid_json"), ("[]", "invalid_structure")):
        result = parse_answer_contract(raw, manifest=manifest, registry=registry)
        assert isinstance(result, ContractRejection) and result.reason_code == code


def test_claim_rejection_names_claim(manifest, registry):
    body = mutate([*CLAIM, "calculation", "op"], "eval")(valid_contract(manifest))
    result = parse(body, manifest, registry)
    assert isinstance(result, ContractRejection) and result.claim_id == "c1"


def test_does_not_check_arithmetic(manifest, registry):
    """Phase 3 validates references only; a wrong value is the Phase 4 gate's job."""
    body = mutate([*CLAIM, "reported_value"], "1.00")(valid_contract(manifest))
    assert isinstance(parse(body, manifest, registry), AnswerContract)


def test_manifest_for_prompt_is_minimal_and_textual(manifest):
    view = manifest_for_prompt(manifest)
    assert set(view) == {"request_id", "result_id", "question", "bindings", "row_refs", "completeness"}
    assert view["row_refs"]["r0"] == {"category": "Electronics", "total_profit": "5000.10"}
    assert view["completeness"] == "complete"
    metric = [b for b in view["bindings"] if b["kind"] == "metric"][0]
    assert metric["metric"] == "total_profit" and metric["unit"] == "money"
    assert "plan_id" not in view and "registry_version" not in view
