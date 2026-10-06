"""Hermetic tests for the Phase 4 deterministic calculation gate."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, cast

import pytest

from src.analysis_contracts import (
    ANSWER_CONTRACT_SCHEMA_VERSION,
    AnswerContract,
    Claim,
    ClaimCalculation,
    ClaimInput,
    ContractRejection,
    Interpretation,
    parse_answer_contract,
)
from src.analysis_validation import (
    REASON_CODES,
    EvaluationError,
    GateVerdict,
    evaluate_claim,
    format_display,
    round_half_up,
    validate_answer,
)
from src.semantic_query import MetricBinding, ResultManifest
from src.semantics.registry import MetricRegistry, load_registry

RID = "result-1"
QUESTION = "What were net sales by category in 2025?"


@pytest.fixture(scope="module")
def registry() -> MetricRegistry:
    return load_registry()


def metric_b(metric_id: str, unit: str = "money") -> MetricBinding:
    return MetricBinding(metric_id, "metric", metric_id, unit, "AUD" if unit == "money" else None, "group")


def dim_b(dim_id: str) -> MetricBinding:
    return MetricBinding(dim_id, "dimension", dim_id, None, None, dim_id)


def make_manifest(rows: list[dict[str, Any]], bindings: tuple[MetricBinding, ...], *,
                  completeness: str = "complete") -> ResultManifest:
    return ResultManifest(
        result_id=RID, request_id="req-1", question=QUESTION, registry_version="1.0.0", plan_id="plan-1",
        execution_id="run-1", job_id="job-1", output_schema=tuple(b.binding_id for b in bindings),
        bindings=bindings, grouping_grain="category" if any(b.kind == "dimension" for b in bindings) else "dataset",
        filters=(), population="fact_sales lines in 2025",
        row_refs={f"r{i}": dict(r) for i, r in enumerate(rows)}, row_count=len(rows),
        completeness=completeness,  # type: ignore[arg-type]
        completeness_basis="test")


CATEGORY_SALES = [("Beverages", "6000.00"), ("Fruits", "5000.00"), ("Stationery", "4000.00"),
                  ("Personal Care", "3800.65"), ("Household", "3000.00"), ("Electronics", "3000.00")]


def grouped(completeness: str = "complete", rows=None) -> ResultManifest:
    rows = rows if rows is not None else [{"category": c, "total_net_sales": Decimal(v)} for c, v in CATEGORY_SALES]
    return make_manifest(rows, (dim_b("category"), metric_b("total_net_sales")), completeness=completeness)


def scalar(value: Any, metric_id: str = "total_net_sales", unit: str = "money") -> ResultManifest:
    return make_manifest([{metric_id: value}], (metric_b(metric_id, unit),))


def cell(row: str, binding: str = "total_net_sales") -> ClaimInput:
    return ClaimInput(RID, binding, row)


def col(binding: str = "total_net_sales", scope: str | None = "complete_group_population") -> ClaimInput:
    return ClaimInput(RID, binding, None, scope)


def nested(op: str, *inputs: Any) -> ClaimCalculation:
    """Nested calculations are a gate-side extension; the contract dataclass types inputs as ClaimInput."""
    return ClaimCalculation(op, cast(tuple[ClaimInput, ...], inputs))


def claim(cid: str, metric: str, op: str, inputs: tuple, value: str, *, role: str = "direct_answer",
          entity: dict[str, str] | None = None, evidence: tuple[str, ...] = (), fmt: str = "money_2dp") -> Claim:
    return Claim(cid, metric, role, ClaimCalculation(op, inputs), value, entity or {}, evidence, fmt)


def contract(manifest: ResultManifest, claims: list[Claim], *, limitations: tuple[str, ...] = ()) -> AnswerContract:
    return AnswerContract(
        schema_version=ANSWER_CONTRACT_SCHEMA_VERSION, request_id=manifest.request_id, result_id=manifest.result_id,
        question=manifest.question, received_metric_ids=manifest.metric_ids, answer_metric_ids=manifest.metric_ids,
        interpretation=Interpretation("p", (), (), None, None, None), claims=tuple(claims),
        limitations=limitations, follow_up=())


def per_row_claims(m: ResultManifest, metric: str = "total_net_sales", role: str = "supporting") -> list[Claim]:
    out = []
    for ref, row in m.row_refs.items():
        out.append(claim(f"row_{ref}", metric, "identity", (cell(ref, metric),), str(row[metric]), role=role,
                         entity={"category": row["category"]}))
    return out


def gate(m, claims, registry, **kw) -> GateVerdict:
    limitations = kw.pop("limitations", ())
    kw.setdefault("question_metric_ids", ())
    return validate_answer(contract(m, claims, limitations=limitations), m, registry, **kw)


def assert_reject(verdict: GateVerdict, code: str) -> None:
    assert verdict.status == "reject", verdict
    assert verdict.reason_code == code, verdict
    assert code in REASON_CODES


# --------------------------------------------------------------------------- rounding

def test_round_half_up_pin_10_365(registry):
    assert round_half_up(Decimal("10.365"), 2) == Decimal("10.37")
    assert format_display(Decimal("10.365"), "average_listing_price_per_product", registry) == "10.37"
    assert format_display(Decimal("-10.365"), "average_listing_price_per_product", registry) == "-10.37"
    assert format_display(Decimal("58.1536133"), "profit_margin_pct", registry) == "58.2"


@pytest.mark.parametrize("reported,ok", [("10.37", True), ("10.36", False), ("10.365", True), ("10.4", False)])
def test_tie_pin_through_gate(registry, reported, ok):
    m = scalar(Decimal("10.365"), "average_listing_price_per_product")
    c = claim("c1", "average_listing_price_per_product", "identity",
              (cell("r0", "average_listing_price_per_product"),), reported)
    v = gate(m, [c], registry)
    assert (v.status == "pass") is ok, v
    if reported == "10.36":
        assert v.reason_code == "value_mismatch"
    if reported == "10.4":
        assert v.reason_code == "imprecise_value"


# --------------------------------------------------------------------------- scalar

def test_correct_scalar_claim_passes(registry):
    m = scalar(Decimal("24800.65"))
    v = gate(m, [claim("c1", "total_net_sales", "identity", (cell("r0"),), "24800.65")], registry)
    assert v.status == "pass", v
    assert v.checked_claims[0].recomputed_value == "24800.65"
    assert v.checked_claims[0].display_value == "24800.65"


def test_wrong_value_rejects(registry):
    m = scalar(Decimal("24800.65"))
    v = gate(m, [claim("c1", "total_net_sales", "identity", (cell("r0"),), "24800.66")], registry)
    assert_reject(v, "value_mismatch")
    assert v.claim_id == "c1" and v.checked_claims[0].ok is False


def test_fewer_decimals_rejects(registry):
    m = scalar(Decimal("24800.00"))
    assert_reject(gate(m, [claim("c1", "total_net_sales", "identity", (cell("r0"),), "24800")], registry),
                  "imprecise_value")


@pytest.mark.parametrize("raw", [24800.65, "24800.65", Decimal("24800.65")])
def test_cell_types_convert(registry, raw):
    m = scalar(raw)
    assert gate(m, [claim("c1", "total_net_sales", "identity", (cell("r0"),), "24800.65")], registry).status == "pass"


def test_null_cell_rejects(registry):
    m = scalar(None)
    assert_reject(gate(m, [claim("c1", "total_net_sales", "identity", (cell("r0"),), "0.00")], registry), "null_cell")


@pytest.mark.parametrize("raw,code", [(float("nan"), "non_finite_cell"), (Decimal("Infinity"), "non_finite_cell"),
                                      ("1e5", "non_numeric_cell"), (True, "non_numeric_cell")])
def test_bad_cells_reject(registry, raw, code):
    m = scalar(raw)
    assert_reject(gate(m, [claim("c1", "total_net_sales", "identity", (cell("r0"),), "100000.00")], registry), code)


@pytest.mark.parametrize("reported", ["NaN", "1e5", "Infinity", "1,000.00", ""])
def test_bad_reported_value_rejects(registry, reported):
    m = scalar(Decimal("100000.00"))
    assert_reject(gate(m, [claim("c1", "total_net_sales", "identity", (cell("r0"),), reported)], registry),
                  "invalid_reported_value")


def test_scalar_direct_answer_must_be_identity(registry):
    m = scalar(Decimal("24800.65"))
    c = claim("c1", "total_net_sales", "sum", (col(),), "24800.65")
    assert_reject(gate(m, [c], registry), "scalar_not_identity")


def test_question_number_laundering_rejects(registry):
    # "Was it above 30000.00?" -> 30000.00 appears in the question but is not derivable.
    m = scalar(Decimal("24800.65"))
    assert_reject(gate(m, [claim("c1", "total_net_sales", "identity", (cell("r0"),), "30000.00")], registry),
                  "value_mismatch")


def test_unknown_references_reject(registry):
    m = scalar(Decimal("1.00"))
    assert evaluate_claim(claim("c", "total_net_sales", "identity", (cell("r9"),), "1.00"), m, registry) \
        == EvaluationError("unknown_row_ref", "row ref 'r9' is not in the manifest")
    assert_reject(gate(m, [claim("c1", "total_net_sales", "identity", (ClaimInput("other", "total_net_sales", "r0"),),
                                 "1.00")], registry), "unknown_result_id")
    assert_reject(gate(m, [claim("c1", "total_net_sales", "identity", (ClaimInput(RID, "nope", "r0"),), "1.00")],
                       registry), "unknown_binding")


def test_model_supplied_constant_rejects(registry):
    m = scalar(Decimal("0.5816"), "effective_discount_rate", "fraction")
    c = claim("c1", "effective_discount_rate", "multiply",
              (cell("r0", "effective_discount_rate"), Decimal("100")), "58.16")  # type: ignore[arg-type]
    assert_reject(gate(m, [c], registry), "model_constant")
    c2 = claim("c1", "effective_discount_rate", "multiply",
               (cell("r0", "effective_discount_rate"), ClaimInput("constant", "100")), "58.16")
    assert gate(m, [c2], registry).status == "reject"


def test_zero_claims_never_pass(registry):
    m = scalar(Decimal("1.00"))
    assert_reject(gate(m, [], registry), "missing_direct_answer")
    supporting_only = [claim("c1", "total_net_sales", "identity", (cell("r0"),), "1.00", role="supporting")]
    assert_reject(gate(m, supporting_only, registry), "missing_direct_answer")
    # and the parser already rejects a number-free contract
    body = {"schema_version": "1", "request_id": "req-1", "result_id": RID, "question": QUESTION,
            "received_metric_ids": ["total_net_sales"], "answer_metric_ids": ["total_net_sales"],
            "interpretation": {"population": "p", "filters": [], "dimensions": [], "grain": None,
                               "weighting": None, "denominator": None},
            "claims": [], "limitations": [], "follow_up": []}
    assert isinstance(parse_answer_contract(json.dumps(body), manifest=m, registry=registry), ContractRejection)


# --------------------------------------------------------------------------- grouped aggregates

def test_sum_over_complete_column_passes(registry):
    m = grouped()
    c = claim("c1", "total_net_sales", "sum", (col(),), "24800.65", entity={"population_id": "categories"})
    v = gate(m, [c] + per_row_claims(m), registry)
    assert v.status == "pass", v


def test_sum_over_subset_rejects(registry):
    m = grouped("subset")
    c = claim("c1", "total_net_sales", "sum", (col(),), "24800.65")
    assert_reject(gate(m, [c], registry, limitations=("population_incomplete",)), "incomplete_population")
    assert evaluate_claim(c, m, registry).reason_code == "incomplete_population"  # type: ignore[union-attr]


def test_subset_identity_needs_limitation(registry):
    m = grouped("subset")
    claims = per_row_claims(m, role="direct_answer")
    assert_reject(gate(m, claims, registry), "missing_population_limitation")
    assert gate(m, claims, registry, limitations=("population_incomplete",)).status == "pass"


def test_column_input_needs_scope(registry):
    m = grouped()
    c = claim("c1", "total_net_sales", "sum", (col(scope=None),), "24800.65")
    assert_reject(gate(m, [c] + per_row_claims(m), registry), "missing_scope")


@pytest.mark.parametrize("reported,ok", [("4133.44", True), ("4133.45", False), ("4133.4417", True)])
def test_mean_of_group_totals(registry, reported, ok):
    m = grouped()
    c = claim("c1", "average_group_total_sold_groups", "mean", (col(),), reported)
    v = gate(m, [c] + per_row_claims(m), registry)
    assert (v.status == "pass") is ok, v


def test_mean_claimed_as_other_metric_rejects(registry):
    m = grouped()
    c = claim("c1", "total_profit", "mean", (col(),), "4133.44")
    assert_reject(gate(m, [c] + per_row_claims(m), registry), "metric_binding_mismatch")


def test_max_with_correct_and_wrong_entity(registry):
    m = grouped()
    good = claim("c1", "total_net_sales", "max", (col(),), "6000.00", entity={"category": "Beverages"})
    assert gate(m, [good] + per_row_claims(m), registry).status == "pass"
    bad = claim("c1", "total_net_sales", "max", (col(),), "6000.00", entity={"category": "Fruits"})
    assert_reject(gate(m, [bad] + per_row_claims(m), registry), "entity_mismatch")


def test_wrong_entity_identity_rejects(registry):
    m = grouped()
    # correct number (Beverages 6000.00) attached to the wrong category
    c = claim("c1", "total_net_sales", "identity", (cell("r0"),), "6000.00", entity={"category": "Fruits"})
    assert_reject(gate(m, [c] + per_row_claims(m), registry), "entity_mismatch")
    c2 = claim("c1", "total_net_sales", "identity", (cell("r0"),), "6000.00")
    assert_reject(gate(m, [c2] + per_row_claims(m), registry), "missing_entity_ref")


@pytest.mark.parametrize("reported,ok", [("0.2419", True), ("0.2420", False)])
def test_share_one_cell_over_column(registry, reported, ok):
    m = grouped()
    c = claim("c1", "share_of_total", "share", (cell("r0"),), reported, role="supporting",
              entity={"category": "Beverages"}, fmt="fraction_4dp")
    v = gate(m, per_row_claims(m, role="direct_answer") + [c], registry)
    assert (v.status == "pass") is ok, v  # 6000 / 24800.65 = 0.24193...


def test_share_wrong_denominator_rejects(registry):
    m = grouped()
    # explicit denominator = Fruits cell instead of the total: 6000/5000 = 1.2 != reported 0.2419
    c = claim("c1", "share_of_total", "share", (cell("r0"), cell("r1")), "0.2419", role="supporting",
              entity={"category": "Beverages"}, fmt="fraction_4dp")
    assert_reject(gate(m, per_row_claims(m, role="direct_answer") + [c], registry), "invalid_share_denominator")


def test_share_must_carry_fraction_metric(registry):
    m = grouped()
    c = claim("c1", "total_net_sales", "share", (cell("r0"),), "0.24", role="supporting",
              entity={"category": "Beverages"})
    assert_reject(gate(m, per_row_claims(m, role="direct_answer") + [c], registry), "unit_mismatch")


def test_percentage_change_and_zero_baseline(registry):
    m = grouped()
    pc = claim("c1", "percentage_change", "percentage_change", (cell("r0"), cell("r1")), "20.0",
               role="comparison", fmt="percent_1dp")
    assert gate(m, per_row_claims(m, role="direct_answer") + [pc], registry).status == "pass"
    rows = [{"category": "A", "total_net_sales": Decimal("10.00")}, {"category": "B", "total_net_sales": Decimal("0.00")}]
    m0 = grouped(rows=rows)
    pc0 = claim("c1", "percentage_change", "percentage_change", (cell("r0"), cell("r1")), "0.0",
                role="comparison", fmt="percent_1dp")
    assert_reject(gate(m0, per_row_claims(m0, role="direct_answer") + [pc0], registry), "undefined_value")


def test_safe_divide_zero_is_undefined(registry):
    rows = [{"category": "A", "total_net_sales": Decimal("10.00")}, {"category": "B", "total_net_sales": Decimal("0.00")}]
    m = grouped(rows=rows)
    c = claim("c1", "total_net_sales", "safe_divide", (cell("r0"), cell("r1")), "0.00")
    assert evaluate_claim(c, m, registry).reason_code == "undefined_value"  # type: ignore[union-attr]


def test_swapped_metrics_rejects(registry):
    m = make_manifest([{"total_net_sales": Decimal("24800.65"), "total_profit": Decimal("14000.00")}],
                      (metric_b("total_net_sales"), metric_b("total_profit")))
    swapped = claim("c1", "total_net_sales", "identity", (cell("r0", "total_profit"),), "14000.00")
    profit = claim("c2", "total_profit", "identity", (cell("r0", "total_profit"),), "14000.00")
    assert_reject(gate(m, [swapped, profit], registry), "metric_binding_mismatch")


def test_each_metric_binding_needs_direct_answer(registry):
    m = make_manifest([{"total_net_sales": Decimal("24800.65"), "total_profit": Decimal("14000.00")}],
                      (metric_b("total_net_sales"), metric_b("total_profit")))
    only_sales = claim("c1", "total_net_sales", "identity", (cell("r0"),), "24800.65")
    assert_reject(gate(m, [only_sales], registry), "missing_direct_answer")


def test_question_metric_ids_must_be_answered(registry):
    m = scalar(Decimal("1.00"))
    c = claim("c1", "total_net_sales", "identity", (cell("r0"),), "1.00")
    assert gate(m, [c], registry, question_metric_ids=["total_net_sales"]).status == "pass"
    assert_reject(gate(m, [c], registry, question_metric_ids=["total_profit"]), "question_metric_not_answered")


@pytest.mark.parametrize("label", ["Total", "Grand Total", None])
def test_subtotal_row_mixed_in_rejects_doubled_sum(registry, label):
    rows = [{"category": c, "total_net_sales": Decimal(v)} for c, v in CATEGORY_SALES]
    rows.append({"category": label, "total_net_sales": Decimal("24800.65")})
    m = grouped(rows=rows)
    doubled = claim("c1", "total_net_sales", "sum", (col(),), "49601.30")
    assert_reject(gate(m, [doubled], registry),
                  "subtotal_row_in_population")


def test_duplicate_join_rejects_doubled_sum(registry):
    rows = [{"category": c, "total_net_sales": Decimal(v)} for c, v in CATEGORY_SALES]
    rows.append(dict(rows[0]))
    m = grouped(rows=rows)
    c = claim("c1", "total_net_sales", "sum", (col(),), "30800.65")
    v = gate(m, [c], registry)
    assert_reject(v, "duplicate_group_key")


def test_dimension_cell_as_number_rejects(registry):
    rows = [{"category": "2025", "total_net_sales": Decimal("1.00")}]
    m = grouped(rows=rows)
    c = claim("c1", "total_net_sales", "add", (cell("r0", "category"), cell("r0")), "2026.00")
    assert evaluate_claim(c, m, registry).reason_code == "dimension_as_number"  # type: ignore[union-attr]
    assert gate(m, [c], registry).status == "reject"


def test_irrelevant_numbers_reject(registry):
    m = grouped()
    row_count = claim("c1", "total_net_sales", "count", (col(),), "6.00")
    assert_reject(gate(m, [row_count] + per_row_claims(m), registry), "irrelevant_number")
    dims_only = claim("c1", "total_net_sales", "distinct_count", (col("category"),), "6.00")
    assert_reject(gate(m, [dims_only] + per_row_claims(m), registry), "irrelevant_number")


def test_missing_group_coverage_rejects(registry):
    m = grouped()
    claims = per_row_claims(m, role="direct_answer")[:5]  # Electronics (r5) never mentioned
    assert_reject(gate(m, claims, registry), "missing_group_coverage")
    # evidence_refs cover a row too
    claims[0] = Claim(claims[0].claim_id, "total_net_sales", "direct_answer", claims[0].calculation,
                      claims[0].reported_value, claims[0].entity_refs, (f"{RID}:r5:total_net_sales",), "money_2dp")
    # evidence_refs never count toward coverage
    assert_reject(gate(m, claims, registry), "missing_group_coverage")


def test_depth_and_size_limits(registry):
    m = scalar(Decimal("1.00"))
    inner: Any = cell("r0")
    for _ in range(4):
        inner = nested("add", inner, cell("r0"))
    deep = Claim("c1", "total_net_sales", "direct_answer", inner, "5.00", {}, (), "money_2dp")
    assert_reject(gate(m, [deep], registry), "depth_exceeded")
    ok_nested = nested("add", nested("add", cell("r0"), cell("r0")), cell("r0"))
    nested_claim = Claim("c1", "total_net_sales", "supporting", ok_nested, "3.00", {}, (), "money_2dp")
    direct = claim("c2", "total_net_sales", "identity", (cell("r0"),), "1.00")
    assert gate(m, [direct, nested_claim], registry).status == "pass"
    many = [claim(f"c{i}", "total_net_sales", "identity", (cell("r0"),), "1.00") for i in range(51)]
    assert_reject(gate(m, many, registry), "too_many_claims")


def test_duplicate_claim_id_rejects(registry):
    m = scalar(Decimal("1.00"))
    c = claim("c1", "total_net_sales", "identity", (cell("r0"),), "1.00")
    assert_reject(gate(m, [c, c], registry), "duplicate_claim_id")


def test_weighted_mean(registry):
    m = make_manifest([{"category": "A", "unit_price": Decimal("2.00"), "quantity": Decimal("1")},
                       {"category": "B", "unit_price": Decimal("4.00"), "quantity": Decimal("3")}],
                      (dim_b("category"), metric_b("unit_price"), metric_b("quantity", "count")))
    c = claim("c1", "unit_price", "weighted_mean", (col("unit_price"), col("quantity")), "3.50")
    assert evaluate_claim(c, m, registry) == Decimal("3.5")


def test_parsed_contract_flows_through_gate(registry):
    m = grouped()
    claims = []
    for ref, row in m.row_refs.items():
        claims.append({"claim_id": f"c_{ref}", "metric_id": "total_net_sales", "answer_role": "direct_answer",
                       "calculation": {"op": "identity", "inputs": [{"result_id": RID, "binding_id": "total_net_sales",
                                                                     "row_ref": ref}]},
                       "reported_value": str(row["total_net_sales"]), "entity_refs": {"category": row["category"]},
                       "evidence_refs": [], "format_id": "money_2dp"})
    body = {"schema_version": "1", "request_id": "req-1", "result_id": RID, "question": QUESTION,
            "received_metric_ids": ["total_net_sales"], "answer_metric_ids": ["total_net_sales"],
            "interpretation": {"population": "p", "filters": [], "dimensions": ["category"], "grain": "category",
                               "weighting": None, "denominator": None},
            "claims": claims, "limitations": [], "follow_up": []}
    parsed = parse_answer_contract(json.dumps(body), manifest=m, registry=registry)
    assert isinstance(parsed, AnswerContract), parsed
    assert validate_answer(parsed, m, registry, question_metric_ids=()).status == "pass"


# --------------------------------------------------------------------------- end to end

MARGINS = [("Stationery", "59.1783393"), ("Electronics", "58.1536133"), ("Household", "56.6662128"),
           ("Fruits", "55.6918289"), ("Personal Care", "54.2323508"), ("Beverages", "53.7581943")]


def margin_case(override: dict[str, str] | None = None):
    rows = [{"category": c, "profit_margin_pct": Decimal(v)} for c, v in MARGINS]
    m = make_manifest(rows, (dim_b("category"), metric_b("profit_margin_pct", "percent")))
    expected = {"Stationery": "59.2", "Electronics": "58.2", "Household": "56.7", "Fruits": "55.7",
                "Personal Care": "54.2", "Beverages": "53.8"}
    expected.update(override or {})
    claims = [claim(f"c_{ref}", "profit_margin_pct", "identity", (cell(ref, "profit_margin_pct"),),
                    expected[row["category"]], entity={"category": row["category"]}, fmt="percent_1dp")
              for ref, row in m.row_refs.items()]
    return m, claims


def test_golden_profit_margins_pass(registry):
    m, claims = margin_case()
    v = gate(m, claims, registry)
    assert v.status == "pass", v
    assert [c.display_value for c in v.checked_claims] == ["59.2", "58.2", "56.7", "55.7", "54.2", "53.8"]


def test_golden_profit_margin_off_by_one_tenth_rejects(registry):
    m, claims = margin_case({"Electronics": "58.1"})
    v = gate(m, claims, registry)
    assert_reject(v, "value_mismatch")
    assert v.claim_id == "c_r1"
    assert [c.ok for c in v.checked_claims] == [True, False]


def test_reason_codes_are_stable_strings():
    assert all(isinstance(c, str) and c == c.strip() and c.islower() for c in REASON_CODES)
    assert {"value_mismatch", "imprecise_value", "incomplete_population", "entity_mismatch",
            "missing_direct_answer", "missing_group_coverage", "irrelevant_number", "depth_exceeded",
            "model_constant", "undefined_value", "subtotal_row_in_population", "duplicate_group_key"} <= REASON_CODES


# --------------------------------------------------------------------------- hardening

def test_entity_attribution_for_non_identity_ops(registry):
    m = grouped()
    sub = claim("c1", "difference", "subtract", (cell("r0"), cell("r1")), "1000.00", role="comparison",
                entity={"category": "Fruits"})
    assert_reject(gate(m, per_row_claims(m, role="direct_answer") + [sub], registry), "entity_mismatch")
    total = claim("c1", "total_net_sales", "sum", (col(),), "24800.65", entity={"category": "Beverages"})
    assert_reject(gate(m, [total] + per_row_claims(m), registry), "entity_mismatch")


def test_share_whole_must_be_same_binding_column(registry):
    m = make_manifest([{"category": "A", "total_net_sales": Decimal("10.00"), "total_profit": Decimal("5.00")},
                       {"category": "B", "total_net_sales": Decimal("30.00"), "total_profit": Decimal("5.00")}],
                      (dim_b("category"), metric_b("total_net_sales"), metric_b("total_profit")))
    c = claim("c1", "share_of_total", "share", (cell("r0", "total_profit"), col("total_net_sales")), "0.5000",
              role="supporting", entity={"category": "A"}, fmt="fraction_4dp")
    assert_reject(gate(m, [c], registry), "invalid_share_denominator")


def test_count_must_match_claim_metric(registry):
    m = grouped()
    c = claim("c1", "distinct_customer_count", "count", (col(),), "6", fmt="count_0dp")
    assert_reject(gate(m, [c] + per_row_claims(m), registry), "metric_binding_mismatch")


def test_difference_operands_must_share_metric(registry):
    m = make_manifest([{"total_net_sales": Decimal("24800.65"), "total_profit": Decimal("14000.00")}],
                      (metric_b("total_net_sales"), metric_b("total_profit")))
    d = claim("c3", "difference", "subtract", (cell("r0"), cell("r0", "total_profit")), "10800.65")
    base = [claim("c1", "total_net_sales", "identity", (cell("r0"),), "24800.65"),
            claim("c2", "total_profit", "identity", (cell("r0", "total_profit"),), "14000.00")]
    assert_reject(gate(m, base + [d], registry), "metric_binding_mismatch")


def test_question_metric_ids_is_required(registry):
    m = scalar(Decimal("1.00"))
    c = contract(m, [claim("c1", "total_net_sales", "identity", (cell("r0"),), "1.00")])
    with pytest.raises(TypeError):
        validate_answer(c, m, registry)  # type: ignore[call-arg]
