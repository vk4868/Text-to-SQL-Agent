# Semantic metrics and verified numerical answers

Status: Phase 1 is in progress with the user's Claude agent. Phases 2–7 are not authorised until the user explicitly approves the preceding phase's completion.

Current handoff (user-reported): Claude is reloading the three BigQuery tables to resolve the Australian-CSV/live-data mismatch, then will establish the baseline from the current data. The reload is not claimed complete or validated by this document. Codex is updating the plan only and must not duplicate Claude's reload or baseline run.

Prepared: 2026-10-06. Original application revision: `1478190ce2750cc130d229b2c8016001ccbcc635`; observed Phase 1 tooling commit: `a951487`.

## Immediate instructions for Claude

Continue the current Phase 1 session; do not restart completed work or submit duplicate table reload jobs. Read CLAUDE.md and this plan before delegating further work. Fable 5.1 owns orchestration, Sonnet handles predefined tasks, and Opus handles reasoning-intensive work and separate review tasks.

The current execution order is: finish the three reloads -> verify the current Australian data -> run the unchanged baseline -> validate reports and linked evidence -> run offline tests -> commit the before report -> present the Phase 1 review packet -> stop for the user's explicit completion approval.

Use the detailed Phase 1 continuation checklist below. Reuse sufficient existing evidence and make only demonstrated, necessary tooling corrections. Do not implement semantic metrics, the verifier, or publication changes during Phase 1. Do not change the frozen case set or model settings silently.

The completion packet must include the implementation matrix, changed files, commit IDs, reload/data-validation evidence, exact test and evaluation commands with their actual results, report paths, outstanding issues, and elapsed implementation time. Mark the phase **Awaiting user approval**. Record the user's approval before starting Phase 2; neither the orchestrator nor a reviewer can grant it.

## Objective and review agreement

### Interview delivery constraints

- Target about two hours of implementation; stop at three hours even if unfinished. The user reports about one earlier hour mainly spent brainstorming: record that separately, and include actual project work already performed by every implementation agent. Changing agents, branches or scope does not reset the implementation clock. Record actual start/stop and elapsed time. The user confirmed the deadline is 1:00pm tomorrow in Melbourne time; Claude must establish the actual remaining implementation time from its work history.
- Preserve the user's approval boundary after every phase. Review waits do not authorise skipping a gate. Track wall-clock elapsed time and active work separately; until agreed otherwise, treat the hard stop conservatively rather than assuming waiting time is excluded.
- Submit a branch or PR with the real development history. Do not squash, rebase to tidy, amend away mistakes, or fabricate a clean sequence. The currently observed Phase 1 tooling commit is `a951487`; retain it if it is part of the submitted work.
- Include a one-page maximum NOTES.md explaining what actually changed, decisions/trade-offs, measured results, next steps, and where agents helped or caused friction. Draft placeholders must be completed or removed before submission.
- Include the actual agent setup (CLAUDE.md, plan.md and optionally a sanitised transcript excerpt). The Fable/Sonnet/Opus instructions describe intended orchestration, not evidence that a particular model performed a task. Attribute only observed work.
- No secrets or real personal data in the submitted files or commits. Inspect the branch diff, included history, reports and any transcript; do not publish raw environment files, credentials or unreviewed logs. If an actual secret is found, stop publication, rotate it and disclose that security remediation takes precedence over preserving an unsafe history.
- The seven phases below describe a bounded extension of the existing pipeline, not a general-purpose semantic platform or a promise of completion within two hours. Keep the semantic registry, structured claims, deterministic gate and single bounded verifier in the plan. Do not silently drop a phase to make the work appear finished.
- Scope the implementation to the existing evaluation questions and a small, explicitly frozen set of price/average challenges. Implement only the reusable operations and trusted query bindings needed for those shapes. Defer arbitrary-SQL equivalence, a general-purpose query compiler, autonomous multi-agent loops and unrelated refactoring. Reject unsupported interpretations, disclose coverage losses, and retain failures in the evaluation denominators. If time expires, label remaining phases unfinished and describe the next action.
- If the clock expires or the baseline remains blocked, stop and submit the actual partial state with the blocker and next action. Do not invent the before/after measurements, claim completion or rename a US-data baseline as Australian.

See INTERVIEW_SUBMISSION.md for the packaging checklist. Claude should continue its current user-directed reload workflow; this plan update does not restart it, authorise additional cloud resources, or authorise another agent to perform the same mutation. It does not authorise a new feature phase or publication.

Build a semantic layer for the quantitative fields in the existing retail dataset. Require a structured model proposal describing the question, available metrics, answer metric, calculation, scope and numerical claims. Validate that proposal against engine-owned evidence, use a bounded verifier model for semantic review, and publish only a deterministically validated answer.

If the model supplies an unsupported or incorrectly calculated number, block the answer immediately. Do not publish the draft with a warning, silently replace the wrong number, or retry until a judge approves it. A verifier cannot override a deterministic rejection.

This document is a plan, not evidence of implementation or production readiness. Phase 1 has been authorised and is underway with Claude; do not ask the user to authorise starting it again. Approval to start a phase is not approval of its completion. For every subsequent phase, explicit user approval of the preceding phase's completion is required; passing tests, an agent review, or silence does not authorise progression. After every phase, update the implementation matrix and wait for the user's review and approval to proceed.

The existing uncommitted changes to `CLAUDE.md` belong to the user and must be preserved. This revision changes plan.md only. Claude owns live data maintenance and baseline execution; this planning step makes no claim about the outcome of its concurrent work.

### Agent ownership and delegation

- Fable 5.1 orchestrates Claude's workflow: scopes tasks, assigns work, owns integration, tracks elapsed time and prepares each user review packet.
- Sonnet handles small changes and repetitive work with a predefined specification, including fixtures, wiring, documentation and prescribed checks. Escalate ambiguity rather than invent business rules.
- Opus handles semantic definitions, calculation design, ambiguous failures and complex debugging. Use a separate review task for independent review of consequential changes.
- Delegate only bounded tasks inside the currently authorised phase, with allowed files, acceptance criteria and evidence requirements. Avoid concurrent writes to the same files, nested delegation without orchestrator approval, and any parallel work on later phases.
- Reviewer approval is evidence for the user, not permission to start the next phase. Record actual model/role usage rather than attributing work from this intended setup alone.

## Six mandatory requirements and where they are satisfied

These requirements are acceptance criteria, not optional follow-up work.

| User requirement | Execution phase(s) | Required evidence |
|---|---|---|
| 1. Re-run your baseline on the current Australian dataset first | **Phase 1 — Rerun your baseline** | Run the unchanged application pipeline against the verified current dataset before feature edits. Record grounding, runtime and SQL accuracy, dataset/model identity and the before-report commit. The historical report is not a substitute. |
| 2. Make the figures users see correct, not merely flagged; justify the approach | Phases 2–6; measured in Phase 7 | Semantic definitions, trusted result bindings, deterministic arithmetic, bounded semantic verifier and controlled rendering. Reject the whole answer on a detected numerical hallucination. Document the design rationale and remaining limits; warnings alone do not satisfy this requirement. |
| 3. Prevent gaming: an analysis with no numbers cannot pass | Freeze acceptance protocol in Phase 1; implement in Phases 4 and 7 | Required question-relevant numerical coverage. Number-free prose, irrelevant numbers and universal refusal cannot count as passing answerable quantitative cases. Keep failures and blocks in the declared denominators. |
| 4. Commit before and after reports under evaluation/reports/ | Before report in Phase 1; after and comparison reports in Phase 7 | Both reports committed under `evaluation/reports/`, covering grounding, runtime and SQL accuracy with explicit before/after changes, comparable configurations and per-case results. Record commit IDs in the review packets. |
| 5. Tests run without GCP or Ollama, using the scripted LLM client | Every implementation phase; full suite in Phases 6–7 | Use ScriptedLLMClient for generator and verifier roles and fake warehouse collaborators; run pytest with live clients blocked. Keep live baseline/evaluation evidence separate from offline tests. |
| 6. Keep the invariants in CLAUDE.md | Every phase | Check the invariant list below, preserve the user's existing edits, and include relevant regression evidence in each completion packet. |

Phase 1 is measurement of the existing system, not implementation of the semantic layer, verifier or new publication behaviour. If live services are unavailable or the dataset cannot be verified, report the blocker and stop; do not substitute scripted results for a live baseline or advance to Phase 2.

## Current evidence and constraints

- Local CSVs contain 1,260 sales lines, 200 customers and 40 products. Branch cities are Brisbane, Melbourne, Perth and Sydney. Earlier live reconciliation found geography differences. Claude is currently reloading the three tables according to the user; confirm post-reload agreement before baseline execution.
- The committed evaluation is dated 2026-08-25: 11/15 overall, 14/15 SQL execution accuracy and 12/15 analysis grounding. These are historical results, not the new baseline.
- Result analysis currently returns the model draft even when numerical or structural checks fail. CLI and Streamlit display it; the graph can still report success.
- The guard checks whether values are derivable, not whether a value belongs to the claimed entity, metric or calculation. Small integers and excluded sections introduce additional gaps.
- The grounding scorer trusts recorded violation lists. Number-free prose can pass. Downstream metrics are omitted for failed runs, making denominator discipline essential.
- BigQuery results currently include rows and job metadata, but not a trusted semantic mapping from output columns to business metrics.
- `FakeBigQueryService` returns canned results. It proves orchestration and gate behaviour, not SQL execution accuracy against the CSV dataset.
- The SQL pipeline caps returned rows, and analysis independently caps rows. Neither the length of returned rows nor BigQuery's post-LIMIT row count establishes completeness of the original group population.

## Architecture and trust boundaries

```text
Question + live schema + versioned metric registry
  -> model proposes a typed query intent
  -> code validates intent and compiles supported query shapes
  -> existing SQLExecutionPipeline validates and executes every query
  -> code issues a result manifest with metric bindings and evidence references
  -> analysis model proposes a structured answer contract
  -> deterministic preflight validates contract, bindings and numerical claims
       -> mismatch: reject immediately -> finalize_run
  -> verifier model reviews question/meaning/scope using the same bound evidence
       -> mismatch or ambiguity: reject -> finalize_run
       -> unavailable or malformed response: error -> finalize_run
  -> deterministic publication gate checks all required approvals and coverage
  -> code renders the five-section answer and source references
  -> finalize_run
```

LangGraph remains the only orchestrator. The verifier is one bounded application role behind the existing LLM protocol, not an autonomous agent with arbitrary database access. Start with one verification call and no verifier repair loop. Preserve the existing bounded SQL repair policy, with semantic validation repeated after any supported repair.

### Why a pre-query contract is needed

An alias such as `average_price` does not prove the expression calculated average listing price. The model must not assign a trusted metric label to arbitrary SQL output. For supported quantitative questions, compile SQL from registered metric expressions, approved dimensions, filters, joins and aggregation plans. Dynamic values and group names remain supported; arbitrary unregistered expressions do not become verified by model agreement.

Use a bounded compiler for the supported shapes, not an attempt to prove equivalence of arbitrary SQL. Catalog the existing golden questions before implementing the compiler so coverage losses are visible. Unsupported shapes must return a clear unsupported/clarification outcome, not fall back to the old publish-with-warning path. This may change SQL accuracy; measure it rather than assuming it is unchanged.

Executed SQL and its hash are provenance, not numerical grounding evidence. Claim values must come from actual result cells or approved calculations over them. The question can contain filters and dates, but cannot establish an observed sales amount or price.

### Authority separation

| Component | Owns | Must not do |
|---|---|---|
| Metric registry | Definitions, source expressions, units, grain, aggregation rules | Derive business policy from a model assertion |
| Query intent model | Proposal of metric, dimensions, filters and scope | Invent a new trusted metric or silently settle unresolved ambiguity |
| Query compiler / result binder | Supported SQL shapes and trusted output-to-metric mapping | Treat aliases as proof or bypass SQLExecutionPipeline |
| Analysis model | Structured claims and references to supplied evidence | Supply authoritative totals, units, lineage or completeness flags |
| Deterministic gate | Reference validation, calculations, units, bounds, coverage | Infer unrestricted natural-language meaning or accept a judge override |
| Verifier model | Semantic match/mismatch/ambiguity assessment | Mutate definitions, calculate trusted values, run tools or approve invalid claims |
| Renderer | User-visible numerical statements and citations | Publish unchecked draft text, numeric assertions in optional sections or partial answers after a rejection |

## Metric registry scope

Register every quantitative measure below. Dates, IDs and classifications are supporting dimensions, not quantities just because their representation contains digits.

| Source field | Initial meaning | Required aggregation policy |
|---|---|---|
| `dim_products.list_price` | Advertised product listing price, per user's definition | One value per product; product mean distinct from a sales-weighted mean; no city/history claim without supporting data |
| `dim_products.unit_cost` | Catalog product unit cost | Product grain; distinguish catalog cost from recorded line cost and historical cost |
| `fact_sales.quantity` | Units on a sales line | Sum; define sign/return handling and denominator eligibility |
| `fact_sales.unit_price` | Recorded pre-discount sales-line unit price under the documented formula | Line mean and quantity-weighted mean are separate metrics |
| `fact_sales.discount_pct` | Fractional line discount | Fraction display policy; line mean distinct from effective revenue-weighted discount |
| `fact_sales.gross_revenue` | Unit price multiplied by quantity | Additive over nonduplicated sales lines |
| `fact_sales.discount_amount` | Gross revenue multiplied by discount fraction | Additive monetary discount |
| `fact_sales.net_revenue` | Gross revenue minus discount amount | Additive; default meaning of sales/revenue already in SQL prompt |
| `fact_sales.tax_amount` | Recorded line tax amount | Add recorded values; no inferred GST/tax rate policy |
| `fact_sales.total_price` | Net revenue plus tax on a line | Additive line amount, not a unit/listing price |
| `fact_sales.cost_amount` | Recorded line cost | Add recorded values; document relationship to catalog unit cost |
| `fact_sales.profit_amount` | Net revenue minus line cost | Additive line profit; not net business profit after overhead |
| `fact_sales.reward_points` | Recorded line reward points | Define earned/adjusted meaning before allowing broader loyalty claims |

Each registry entry needs: stable ID, version, label, approved synonyms, description, source table/column or expression, data type, unit/currency, source grain, allowed dimensions/joins, allowed operations, default aggregation if unambiguous, numerator/denominator/weight rules, null and zero policy, sign policy, precision/rounding/display rules, provenance and unsupported uses.

Derived definitions include discounted unit price, total net sales, total units, transaction-line count, distinct customer/product count, profit margin, effective discount rate, average listing price per product, average realised net price per unit, average net sales per line, average group total, differences, shares and percentage changes. Monetary constants must come from evidence; formula constants such as percentage scaling are registry-owned.

Do not confuse order counts with sales-line counts: there is no order identifier in the inspected schema. Define price averages independently from sales averages. A mean of group means requires the appropriate underlying weights; already aggregated inputs cannot be averaged without respecting their grain.

Supporting definitions: primary/foreign keys and join cardinalities; product/category/subcategory/brand; branch geography versus customer home geography; sale date versus signup/launch date; channel, payment, promotion and customer classifications. Current customer/product attributes do not establish historical attributes at the sale date.

### Decisions to resolve during Phase 2 review

- Confirm currency, monetary precision and rounding policy; Australian cities alone do not prove AUD or a tax treatment.
- Confirm which definition applies when a user simply says average price. Recommended: ask for clarification rather than choose an undisclosed weighting.
- Confirm whether average category sales includes categories with no matching sales. Support explicit sold-groups and full-catalog populations as separate definitions.
- Confirm treatment of nulls, zero quantities, returns/negative values and undefined ratios. Unknown policy must remain unsupported, not silently defaulted.
- Listing price is product-level; city-specific or historical listing prices remain unsupported with the current schema.
- Runtime verifier model/provider and added latency budget remain explicit configuration decisions. Using GPT-6 for planning research does not authorise sending database rows to an external hosted model. Default implementation stays compatible with the current local provider and scripted tests.

## Structured contracts

Use separate engine-owned and model-proposed objects. A model cannot create or modify trusted result identifiers, registry versions or completeness evidence by repeating them in JSON.

### Engine-owned result manifest

Contains a request ID and original question, registry version, query-plan ID, execution ID, result ID, output schema, compiler-established metric bindings, grouping grain, filters/population, currency/units, engine-assigned row references, and completeness status with its basis.

Completeness is `complete`, `subset`, or `unknown`, not an LLM boolean. For full-population averages/totals, compile aggregation before outer display limits; preserve numerator/denominator sufficient statistics. Group lists exceeding caps must be identified using a bounded count/metadata strategy or marked incomplete. Extra evidence queries also pass through SQLExecutionPipeline and count toward runtime/cost. Never lift caps merely to make a claim verifiable.

### Analysis-model output

The contract explicitly answers the user's requested fields:

| Field | Purpose | Validation |
|---|---|---|
| `schema_version`, `request_id`, `result_id` | Bind output to the current request and evidence | Exact engine-issued match |
| `question` | Original question being answered | Exact match to engine-owned question; never replace the original |
| `received_metric_ids` | Metrics available to the model | Exact manifest inventory for this invocation |
| `answer_metric_ids` | Metrics selected to answer the question | Registered, supplied/derivable and semantically appropriate |
| `interpretation` | Population, filters, dimensions, grain, weighting and denominator | Conform to validated intent and registry; ambiguity must be explicit |
| `claims` | Numerical answer proposals | Nonempty for answerable quantitative questions; required coverage |
| `claims[].calculation` | Typed operation and references to operands | Allowlisted operations, source refs, bounds, units and granularity |
| `claims[].reported_value` | Number the model proposes to show, encoded as decimal text | Compare with independently recomputed value under the fixed rounding rule |
| `claims[].entity_refs`, `evidence_refs` | Which products/groups and source cells support the claim | Resolve against current result; bind each value to its entity and metric |
| `claims[].answer_role` | Direct answer, comparison or supporting evidence | Coverage of the actual question; no passing through unrelated row counts |
| `limitations`, `follow_up` | Structured reason/template IDs and approved references | No free-form numerical-claim escape hatch |

Illustrative claim, not an implemented schema or a measured result:

```json
{
  "claim_id": "c1",
  "metric_id": "average_category_net_sales",
  "answer_role": "direct_answer",
  "calculation": {
    "op": "mean",
    "input": {
      "result_id": "result-1",
      "binding_id": "category_net_sales",
      "scope": "complete_group_population"
    }
  },
  "reported_value": "7000.00",
  "entity_refs": {"population_id": "sold_categories_for_request"},
  "evidence_refs": ["result-1:category_net_sales"],
  "format_id": "money_2dp"
}
```

The manifest defines the actual population and all operands; the model cannot invent the contents of `result-1`. After parsing, the gate iterates claims, resolves inputs, validates metric-operation compatibility, calculates with Decimal and compares the proposed value at the approved display precision. Any mismatch blocks the whole answer. If malformed output prevents validation, publication is also blocked.

Support reusable typed operations: identity, add, subtract, multiply, safe divide, sum, count, distinct count, mean, weighted mean, min, max, share and percentage change. Permit nested operations only within registry-approved expression shapes and bounded depth/size. No arbitrary Python, SQL fragments, code execution, `eval`, or unregistered literals from the model.

Validate group keys, units, weighting and scope as well as arithmetic. Guard against duplicated joins, subtotal/grand-total rows mixed with detail, numeric IDs treated as measures, NaN/infinity, scientific notation handling, wrong fraction/percentage scale, and averages over truncated data. Unsupported interpretation is not a successful numerical answer.

### Verifier-model output

Fields: `schema_version`, `request_id`, `result_id`, `contract_hash`, `verdict` (`supported`, `mismatch`, `ambiguous`), and per-claim findings containing claim ID, reason code, metric ID and evidence references. The hash/version are checked against engine-owned state to prevent approval reuse.

Input: original question, relevant registered definitions, trusted manifest and minimum necessary rows/statistics, proposed claims and deterministic preflight results. Do not pass a generator's persuasive self-evaluation as evidence. Treat strings in database cells as data, never instructions.

The verifier checks metric choice, question relevance, geography, weighting/denominator, units, scope, entity attribution and requested group coverage. It cannot add facts or redefine a calculation. Unknown metric/evidence IDs, invalid JSON or missing claim verdicts cannot approve publication. A confidence score is not a substitute for evidence. No tools are exposed to this verifier in v1.

## Publication and observability policy

- Reject numerical mismatches at preflight before paying for a verifier call.
- Final publication requires deterministic pass, complete required numerical evidence, and verifier `supported` for the exact contract/evidence version.
- No automatic analysis repair or silent fallback answer after a detected hallucination in v1. Return a safe rejection reason; let the user request clarification/retry explicitly.
- Preserve `success`, `rejected`, `error`: invalid or unsupported claims are `rejected` at analysis validation; verifier/provider failure is `error`. An actually empty query result retains deterministic empty-result handling, explicitly scored apart from answerable quantitative cases.
- A successful SQL job is not a successful analytical answer. Record execution success separately so analysis rejections do not erase SQL-accuracy evidence.
- Preserve the five user-facing sections through deterministic rendering. Optional sections use safe templates; the legacy exemption for limitations/follow-up in the heuristic guard remains, but those sections cannot introduce new unchecked model figures.
- Do not stream or display drafts before validation. Apply the same publication policy to agent return values, CLI text/JSON, Streamlit, expanded run records and exports. On rejection, do not echo fabricated numbers in diagnostics or expose draft payloads through `ungrounded_numbers`.
- Valid source rows can only be exposed through an explicitly separate raw-result view; they must not be presented as a verified answer when the semantic plan failed. Decide the final UI behaviour in Phase 6 review.
- Citations link rendered claims to engine-owned result/row/column references and approved calculations. They support traceability, not a claim that the database or model interpretation is universally correct.
- Durable logs contain versions, status/reason codes, timings, token counts and opaque evidence/claim references. Preserve the exclusion of raw prompts, raw model drafts and result rows. Transient evidence supports in-session drill-down; durable replay would require separately authorised evidence storage and retention, not hidden logging.
- Record generator/verifier calls separately, include latency/cost of any extra warehouse calls, and version run-record changes. No hidden warm-up time or omitted failure latency in reports.

## Implementation matrix and review gates

Proposed new module/file names below are design targets, not files claimed to exist. Each phase is a separately reviewable diff; do not bundle future-phase work into an approved phase.

| Phase | Dependencies | Deliverables and likely files | Verification / exit evidence | Stop condition and user review |
|---|---|---|---|---|
| 1. Rerun your baseline | Already authorised; Claude verifies its current table reload before evaluation | Current-data before report (`evaluation/reports/before_semantic_gate.md` and `.json`) and run manifest, committed with the baseline evidence; reconciliation of local CSVs and BigQuery; fixed case IDs/model settings; capture needed synthetic evaluation evidence separately from production logs | Current 15 golden cases plus 19 attacks; existing offline suite; revision/data/schema/model fingerprints; per-case SQL accuracy, old grounding, durations and unavailable cases | No baseline claim if GCP/Ollama unavailable or dataset mismatch unresolved. Present the baseline, report commit and evaluation protocol; stop until the user explicitly approves Phase 1 completion before any feature code |
| 1. Current execution status (2026-10-06 23:30 AEDT) | Phase 1 work complete; **Awaiting user approval** | Reload: three `bq load --replace` jobs DONE (WRITE_TRUNCATE, 1260/200/40 output rows, 0 bad records), schemas, DAY partitioning on `sale_date` and clustering preserved; US-geography originals backed up to dataset `business_insights_us_backup_20261006`. Validation: numeric 12/12 and column-level 54/54 reconciliation figures match in the report manifest; keyed row-level fingerprints of all three tables and foreign-key orphan counts match (`before_semantic_gate_dataset_check.json`); tables unmodified since 23:05, baseline ran 23:06-23:23. Baseline: `before_semantic_gate.{md,json}`, evidence `before_semantic_gate_runs.jsonl` (45 graph runs + 45 linked executions, redacted, no rows/prompts). Commits: tooling `a951487`, corrections `3d96d16` (evidence missing-link reporting, keyed dataset check), before report and evidence `1edf2f4` | Live: pooled 31/45 (trials 9, 11, 11 of 15); execution_accuracy 42/45; analysis_grounding 33/45; sql_accuracy_any_outcome 42/45; execution coverage 45/45; guardrails 19/19; latency mean 19.2 s, median 20.0 s, p95 26.5 s; 0 rejected/error/unavailable. Offline: 548 hermetic tests pass with and without a real project id; docs-claims 50 pass; pyrefly unchanged at 2 pre-existing errors | Phase 1 packet presented. Implementation time this session: 21:40-23:30 AEDT (~1 h 50 min) plus 21:27-21:40 planning; user's earlier brainstorming hour separate. No Phase 2 work started |
| 2. Metric definitions | User-approved completion of Phase 1 | Versioned registry and loader, proposed `src/semantics/`; all quantitative columns mapped; explicit derived metrics, dimensions, units, grain, join and aggregation policies; decision log | Offline registry validation, schema-field coverage, duplicate IDs, incompatible units, unknown operations and clear hand-calculated examples of every average variant | Currency/rounding and ambiguous average/population rules reviewed. Unsupported definitions marked explicitly. Stop for registry/code review |
| 2. Current execution status (2026-10-07 09:25 AEDT) | Phase 1 completion approved by the user 2026-10-07; Phase 2 work complete; **Awaiting user approval** | `src/semantics/metrics.yaml` (registry 1.0.0: 13 base measures, 23 derived metrics, 23 dimensions, 2 joins, 3 keys, excluded columns, 5 ambiguous phrases with clarify policy, hand-calculated examples), `src/semantics/registry.py` (dataclasses, call-time loader, validator), `src/semantics/DECISIONS.md` (19 decisions proposed for review, fail-closed defaults, ROUND_HALF_UP, known limitations), `tests/unit/semantics/test_registry.py`. Commit `8239488`. No graph, prompt, analysis or evaluation code changed | Offline: 609 hermetic tests pass (579 after implementation, 609 after review fixes); pyrefly 2 pre-existing errors; no I/O at import. Opus implementation and a separate Opus review (15 findings: 2 blocking, 9 should-fix, 4 nits) followed by a Sonnet fix pass covering all 10 prescribed items; orchestrator mutation check: 7/8 silent mutations caught, the remaining gap (money-over-count mean relabelled percent) and the unloaded clarify policy recorded in DECISIONS #18. No second independent re-review after the fix pass (timebox) | Decisions needing the user's confirmation: currency unconfirmed, ROUND_HALF_UP display rounding, bare "average price" clarifies among four candidates, two group-average populations with the filter rule, nulls/negatives/undefined ratios fail closed, transaction = sales line. Implementation time at packet: ~2 h 25 min (Phase 1 ~1 h 50 min + planning 13 min + Phase 2 ~22 min). Phase 3 not started |
| 3. Structured claims and trusted result bindings | User-approved completion of Phase 2 | Typed schemas/parser; trusted query-expression mappings or small templates for the supported shapes and a result binder; result schema/manifest support in BigQuery reader/fakes; generation prompt changes; proposed `src/analysis_contracts.py` and `src/semantic_query.py` | Scripted model parsing; missing/extra fields, invented metric/result IDs; product/city/category and nested group aggregation SQL shapes; grain, join cardinality and completeness tests; live SQL equivalence reserved for Phase 7 | No trusted label based on alias alone; existing question-shape coverage documented. Stop for contract and compiler review |
| 3. Current execution status (2026-10-07 09:55 AEDT) | Phase 2 completion approved by the user (currency AUD, ROUND_HALF_UP, average-price prompt; amendment `3dd0b5e`); Phase 3 bounded slice complete; **Awaiting user approval** | New `src/semantic_query.py` (typed intent parser with forced clarification on ambiguous phrases and request-id check, bounded compiler from registry calculations and joins, engine-owned bindings and result manifest with executed-SQL verification and evidence-based completeness) and `src/analysis_contracts.py` (answer contract, reference-only strict parser, prompt payload), plus tests. Commit `c483d72`. **Not delivered from this row:** result-schema support in the BigQuery reader/fakes, generation prompt changes, nested group aggregation shapes, graph wiring (Phase 6) | Offline: 737 hermetic tests pass; pyrefly 2 pre-existing errors. Live (read-only, 4 compiled queries through SQLExecutionPipeline on the reloaded data): values equal the expected-facts annex; 14 of 15 golden shapes compile, average-discount requires clarification first. Opus implementation, separate Opus review (3 blocking, 7 should-fix, 3 nits), Sonnet fix pass closing the blocking items; orchestrator probes confirmed forced clarification and forged executed-SQL rejection; no second re-review | Open for Phase 4/6: free-text interpretation fields and claim ids may carry digits, limitation/follow-up template catalog, mutable row_refs, cap source in completeness basis. Implementation time: ~2 h 20 min (Phase 1 ~1 h 30 min excluding the baseline run and reload wait, planning 13 min, Phase 2 ~22 min, Phase 3 ~15 min). Phases 4-7 not started |
| 4. Deterministic calculation gate | User-approved completion of Phase 3 | Decimal evaluator and preflight gate, proposed `src/analysis_validation.py`; structured result analysis prompt; positive evidence/coverage checks | Hand-authored expected results for primitives/compositions; wrong denominator, wrong entity, swapped metrics, false values, null/zero, rounding, insufficient rows, missing groups, question-number laundering and duplicate joins; malicious expression rejection | Every injected mismatch blocks, including a correct number attached to the wrong entity. No unrelated number can meet coverage. Stop for gate review |
| 4. Current execution status (2026-10-07 10:12 AEDT) | Phase 3 completion approved by the user; Phase 4 bounded slice complete; **Awaiting user approval** | New `src/analysis_validation.py` (Decimal evaluator, ROUND_HALF_UP comparison at registry precision, entity/metric/unit/scope/coverage rules, whole-answer rejection with stable reason codes) and tests. Commit `a19aaf5`. **Not delivered:** structured result-analysis prompt, registry-owned constants in claims, graph wiring (Phase 6) | Offline: 801 hermetic tests pass; pyrefly 2 pre-existing errors. Live (read-only, compiler -> pipeline -> binder -> parser -> gate on 2025 category margins): correct claims pass; value error, wrong entity, missing group, laundered evidence ref and imprecise value each block. Opus implementation, separate Opus review (4 blocking, 4 should-fix, 3 nits), Sonnet fix pass closing all 4 blocking items plus the difference-operand hole and a required question-metric argument; orchestrator re-probe confirmed; no second re-review | Open for Phase 5/6: subtotal-row guard is exact-label only, group coverage not enforced above 12 rows, swapped percentage-change inputs left to the verifier, weighted_mean unreachable, format_id not checked. Implementation time: ~2 h 35 min (Phase 4 ~14 min, 09:57-10:11). Phases 5-7 not started |
| 5. Bounded verifier role | User-approved completion of Phase 4 | Proposed `src/analysis_verifier.py` and verifier prompt; injectable verifier LLM via existing Protocol; typed verdict; per-role call accounting | Separate ScriptedLLMClient queues; support/mismatch/ambiguity/provider failure/invalid schema; fabricated refs, stale approval, data-cell instructions and a verifier approving a deterministically invalid claim | One bounded call; zero call on deterministic failure; no override or recursive tool loop. Model behaviour quality remains unproven until measured. Stop for verifier review |
| 6. Enforce publication across the application | User-approved completion of Phase 5 | Integration in graph state/nodes/builder and agent; deterministic renderer; run-record versioning; CLI/Streamlit; both fake and live wiring updated; documentation reflecting final behaviour | Entire offline suite plus sink-level CLI JSON/terminal and Streamlit AppTest checks; no leaked drafts; one final record on every path; successful execution with rejected analysis recorded accurately; new node timings and tokens accounted | No old warning-only escape path in the full application; smaller graph slices still work. Show reviewed successful, rejected and unavailable examples. Stop for end-to-end code/UI review |
| 7. Measure and prepare the submission | User-approved completion of Phase 6 | New independent metric scorers, committed after and comparison reports alongside the preserved before report, README/PIPELINE updates, reproducible commands, one-page NOTES.md, actual agent setup and a branch/PR retaining real commit history; proposed reports `before_semantic_gate.*`, `after_semantic_gate.*`, `semantic_gate_comparison.md` | Same current dataset/model/cases and frozen scoring protocol; separate expanded suite; live SQL comparison and full-request latency; offline tests without clients; all case denominators, coverage, unsafe publication and verifier contribution disclosed | No claim of completion if baseline/current data incomparable, live measurement missing, unsafe publication observed, or requested metrics absent. Present full diff/report and wait for final user review |

For each review packet include: phase scope, changed files, metric/requirement-to-test matrix, commands actually executed, exact results, discrepancies and resolutions, sample outputs, remaining limitations, and the next phase proposed. Tests accompany the implementation they validate rather than being postponed to Phase 7. Update this matrix with actual evidence and user approval status after each phase. Mark completed work as **awaiting user approval**, not approved. Record the user's explicit completion approval before beginning the next phase; no phase runs ahead in parallel.

### Phase 1 continuation: reload, validate, baseline, review

Claude should continue its existing work rather than restart or duplicate the reload:

1. Inspect the actual reload job states for fact_sales, dim_customers and dim_products. Confirm completion for all three, record relevant job/source identities and preserve the prior schema, partitioning and clustering where required. Do not evaluate during a partial reload.
2. Confirm current Australian geography, expected row counts (1,260 / 200 / 40), key uniqueness, foreign-key validity and schema compatibility. Verify row-associated values using canonical keyed comparison or equivalent existing reload evidence. Matching totals and per-column distinct sets alone do not prove that prices, products, cities and categories remained associated correctly. Reuse existing evidence where sufficient; do not build another generic reconciliation framework.
3. Keep the application SQL generation, analysis behaviour and model settings unchanged for the before run. Use the existing frozen cohort/protocol and named report tooling; do not add feature code or weaken criteria to get a baseline. Any necessary protocol correction must be explicit and applied equally to the later after run.
4. Run the live baseline only after the data check succeeds. Capture grounding, runtime, SQL accuracy, trial/case counts, failures, data/model/configuration identity and exact commands. Preserve the old latest report as historical evidence.
5. Check report and evidence integrity: every attempted case/trial has an outcome, expected graph records are present, and linked SQL-execution records are accounted for. A preliminary reviewer flagged possible silent omission of linked execution records; verify this with a focused test before treating it as a confirmed defect. Correct only demonstrated issues necessary for trustworthy evidence.
6. Run the offline tests with scripted/fake collaborators. Commit the before reports and permitted synthetic evidence under evaluation/reports/, preserving real history and excluding secrets/real personal data. Do not claim canned results demonstrate live SQL accuracy.
7. Update the matrix with actual results, commands, changed files, commit IDs, unresolved issues and elapsed time. Present the Phase 1 completion packet and stop. Only explicit user approval of completion unlocks Phase 2.

### Time and scope checkpoints

At the two-hour implementation checkpoint, assess remaining work and prioritise honest reporting and handoff over new architecture. At three hours, stop implementation even if the feature or after report is unfinished. Keep the semantic registry, structured contract, deterministic gate and bounded verifier as separately reviewable steps; report unfinished steps rather than silently omitting them. Include time already spent by other agents, with the user's earlier brainstorming hour recorded separately.

## Evaluation protocol: prevent gaming and preserve comparability

Freeze before implementing the feature:

1. Keep all existing golden case IDs and reference SQL as the historical comparison cohort. Preserve all attack cases. Add a separately reported numerical/semantic challenge cohort instead of replacing hard cases.
2. Define independent expected metrics, group identities, weighting, relevant numerical facts and minimum answer coverage for each supported challenge. Expected outputs must be hand-derived/reference-derived, not generated by the production gate or verifier.
3. Score actual rendered answers for both baseline and new system with the same answer-level rubric. Do not require the old system to emit the new JSON contract; report contract compliance separately. The old heuristic grounding score remains a labelled historical comparator.
4. Define answer success as relevant numerical coverage AND correct entity/metric attribution AND correct values/scope. Number-free prose, a year copied from the question, only a row count, missing analysis and universal refusal fail on answerable numerical cases.
5. Report unsafe published answers / all attempted cases; useful verified answers / all answerable cases; numerical claim precision with its denominator; required-fact/group coverage; blocks, errors and unavailable cases separately. A zero-claim answer has no claim precision, not 100%. Genuine empty results and intentionally ambiguous cases have separate expected-outcome measures.
6. Measure SQL execution accuracy whenever a SQL result exists, even if the subsequent analysis is blocked. Also report execution coverage over all attempts. Unsupported cases stay in overall denominators. Keep the old scorer for comparability but add semantic column/metric binding checks: sorting values inside a row can hide swapped numerical columns.
7. Record mean, median and p95 end-to-end latency using a stated percentile convention, plus per-role/node latency, failures, calls/tokens and warehouse work. Compare same hardware/provider/model identity and configuration; disclose cache state and cold/warm runs. Freeze repetition count before running and publish every trial, not the best one.
8. Compare full verifier mode to deterministic-only mode on the same frozen candidate contracts in an offline/shadow experiment. This isolates additional semantic detections, false approvals, false rejections and latency. The shadow mode is not a user-facing bypass. The production verifier never grades its own benchmark performance.
9. Distinguish live model/SQL evidence, fixed-input replay, and scripted gate tests. Scripted 1 ms LLM timings are not a runtime benchmark; canned fake query results are not SQL correctness evidence.
10. Before/after manifests include Git revision and dirty-diff identity, case/reference/registry versions, CSV hashes, live schema/data reconciliation, model identity beyond a mutable tag when available, provider settings, caps, date and environment. Data changes invalidate a paired comparison unless rerun on a matching snapshot.

Preserve the historical `latest.*` until a current replacement and matching documentation are reviewed. Use explicit before/after names because `evaluation/reports/report-*` is gitignored. Reports and approved code belong in reviewable commits; never commit credentials, production rows or raw model payloads. For the bundled synthetic dataset only, a separately documented evaluation artifact may store the minimum baseline response/evidence necessary for independent rescoring; keep that mechanism distinct from durable application logging.

Minimum challenge coverage: all primitive operations; weighted/unweighted means; mean of group totals; multi-dimensional regrouping; absent/zero-sales groups; product listing versus transaction price; wrong geography; mixed units/scales; entity swaps; IDs as quantities; derived values absent from raw rows; wrong count basis; zero/null/negative values; rounding boundaries; empty results; excessive row counts; SQL and analysis truncation; malicious strings; fabricated metrics/evidence; number-free and irrelevant-number answers; verifier disagreement and unavailability; stale result reuse; no-leak publication across all sinks.

## Invariants to retain

- Every database execution passes through SQLExecutionPipeline, including reference/evidence queries; retain read-only checks, table allowlist, row/cost/time caps and live schema grounding.
- LangGraph is the sole orchestrator; preserve the smaller graph builders. All full-graph terminal paths go through finalize_run, with the existing outer crash-record fallback.
- Reducer-backed lists receive only new entries. Preserve one graph record per run, structured boundary outcomes and positive evidence for success.
- Keep SQL repair policy centralised; analysis verification does not introduce a new SQL repair policy or unbounded retry loop.
- LLM roles use the existing protocol and scripted client. No I/O at import, no test-time live clients, and configuration remains overridable through the existing seams.
- Streamlit remains presentation-only. New publication logic belongs below both user interfaces.
- Raw SQL/model text cannot serve as evidence for numerical facts. Preserve raw-data exclusions from durable logging.
- Keep the existing five-section presentation contract and the legacy guard's section-scoping rule; close publication loopholes through typed rendering rather than pretending regex can understand every sentence.
- Update all dependency manifests if a new dependency is justified; prefer existing libraries and standard-library dataclasses/Decimal where sufficient. Do not opportunistically fix the documented pre-existing type-check errors.
- Run documentation-claim checks when changing the docs they govern. Preserve the user's CLAUDE.md edits; distinguish newly enforced guarantees from legacy heuristic limitations when its description is updated.

## Research findings

One research subagent, inheriting the parent model without an override, reviewed primary sources. It spawned no additional agents. These sources support component patterns; the combined design above is our proposal, not a claimed company implementation or an industry guarantee.

| Primary source | Documented evidence | Application and limit |
|---|---|---|
| [Anthropic production multi-agent Research](https://www.anthropic.com/engineering/multi-agent-research-system) | Orchestrator/workers and a dedicated CitationAgent; discusses coordination complexity, token overhead and evaluation | Separate responsibilities and preserve attribution. A citation agent is not proof of numerical correctness; our narrow workflow does not need a research swarm |
| [Morgan Stanley / OpenAI deployment account](https://openai.com/index/morgan-stanley/) | Expert-informed predeployment evaluations, regression questions and human review of Debrief outputs | Use labelled cases and user review gates. This source does not disclose a multi-agent numerical verification gate |
| [dbt Semantic Layer architecture](https://docs.getdbt.com/docs/use-dbt-semantic-layer/sl-architecture) | Central metric definitions, dimensions and relationships drive SQL generation | Use a versioned registry and trusted query mapping; adopting dbt itself is not required for this small project |
| [OpenAI Structured Outputs](https://openai.com/index/introducing-structured-outputs-in-the-api/) | Schema-constrained outputs improve structural reliability, but values can still be incorrect | A schema is a parsing/contract boundary, not a factual guarantee. Our existing local LLM protocol still needs explicit parsing and validation |
| [Anthropic agent evaluations](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) | Code, model and human graders serve different roles; model graders need calibration; operational metrics matter | Independent arithmetic/coverage evaluation, labelled semantic cases and verifier ablation; do not let a production verifier score itself |
| [LLM-as-a-judge research](https://arxiv.org/abs/2306.05685) | Reports biases and reasoning limitations of model judges | Measure false approvals and false rejections. Separate context or a different model does not establish independent proof |

The research reinforces four limits: post-query labels cannot prove SQL meaning; engine-owned completeness and source bindings are essential; fail-closed publication can reduce availability and must be paired with answer-coverage metrics; correct citations/calculations do not establish that source data or an ambiguous interpretation is correct.

## Approval status

Historical checks from the initial planning pass on 2026-10-06 (not current Phase 1 completion evidence):

- The same sole research subagent performed a separate read-only design review of the initial draft against the user requirements and CLAUDE.md. It reported no blocking design discrepancies; it made no edits, ran no tests and spawned no additional agents. This is not implementation validation.
- The main agent ran `.venv/bin/python -m pytest tests/unit/test_docs_claims.py -q`; it passed. This checks the existing governed documentation, not the correctness of the proposed feature or every detail in this new plan.
- At the initial planning pass, repository status showed the new plan plus the user's pre-existing CLAUDE.md modification. That pass did not run a baseline or implement the application feature. Later Phase 1 work and packaging files must be assessed from their own evidence.

| Item | Status |
|---|---|
| Revised execution plan | Updated at user request; phase-completion approvals remain mandatory |
| Phase 1 baseline | Complete and committed (before report, evidence, dataset check); **awaiting user approval of Phase 1 completion** |
| Phase 2 metric definitions | Complete; completion approved by the user 2026-10-07 |
| Phase 3 contracts and compiler | Bounded slice complete; completion approved by the user 2026-10-07; reader/prompt wiring deferred to Phase 6 |
| Phase 4 deterministic gate | Bounded slice complete and committed (`a19aaf5`); documentation commit `edc77ca`. The user decided on 2026-10-07 to stop implementation at Phase 4 |
| Phases 5–7 implementation | Not started; stopped at Phase 4 by the user's decision on 2026-10-07; the remaining work is described as next steps in NOTES.md |
| Live validation | Earlier read-only checks found the mismatch; post-reload validation and current-data baseline are Claude’s responsibility and not claimed complete here |
| Production readiness | Not claimed |
