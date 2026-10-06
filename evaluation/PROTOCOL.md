# Evaluation protocol for the semantic-gate before/after comparison

Status: frozen on 2026-10-06 at the start of Phase 1, before any feature code.
Governs the Phase 1 "before" report and the Phase 7 "after" and comparison
reports. Changing a "frozen" rule after the before report is committed
invalidates the paired comparison unless both sides are rerun under the new
rule. The commit that introduces this file also introduces the evaluation
flags it names; the review packet records that commit id.

## 1. Frozen cohort

| File | Cases | SHA-256 at freeze |
|---|---|---|
| `evaluation/golden_cases.yaml` | 15 questions with hand-written reference SQL | `32c8e14f81875d9dd3fe38aa555aba04b6c510b1a940a184c26e131b5938da92` |
| `evaluation/attack_cases.yaml` | 19 hostile queries the guardrails must refuse | `47ad624d2528ddd106e699bd2d872abd9d5630f1ba4efcfb8f4f009b4587a6f1` |
| `evaluation/golden_expected_facts.yaml` | reference-derived expected facts and minimum coverage per golden case (section 4); geography labels follow the CSVs | recorded in the before-report manifest under `files` |

Golden case ids, in file order: `net_sales_by_month_2025`,
`total_net_sales_2025`, `total_profit_2025`, `profit_by_category_2025`,
`top_5_products_by_revenue_2025`, `revenue_by_region_2025`,
`electronics_november_december_2025`, `sales_by_membership_status_2025`,
`transaction_count_2025`, `average_discount_by_promotion_2025`,
`profit_margin_by_category_2025`, `sales_by_channel_2025`,
`quantity_sold_by_category_2025`, `best_month_for_profit_2025`,
`no_sales_in_1999`.

Rules:

- No golden or attack case is removed, reworded or given a new reference SQL
  during Phases 2-7. A hard case stays in the cohort even if it fails.
- 14 golden cases are answerable quantitative cases. `no_sales_in_1999` is
  the deliberate empty-result case; its expected outcome is in section 4.
- Denominators: attempted = 15 per trial, 45 pooled; answerable = 14 per
  trial, 42 pooled; empty-result = 1 per trial, 3 pooled. Every attempted
  run stays in its denominator whatever its outcome.
- A separately reported numerical/semantic challenge cohort is required
  before the Phase 7 comparison (section 10). It is reported on its own and
  never merged into these denominators.

### Project identifier

The YAML files reference the placeholder project `your-project-id`, which is
also the default of `GCP_PROJECT_ID`. The loaders substitute the configured
project id at load time; every committed artifact is redacted back to the
placeholder. The substitution changes identifiers only, never the semantics
of a reference query.

## 2. Classes of evidence

Every number in a report is labelled with exactly one of these classes:

| Class | What it is | What it can prove |
|---|---|---|
| Offline hermetic tests | `uv run pytest tests/` with `ScriptedLLMClient` and `FakeBigQueryService` | Orchestration, gate and scoring logic. Never SQL accuracy or model quality. |
| Scripted replay (does not exist yet; Phase 4 onward) | Fixed model outputs replayed through the real gate code | Gate behaviour on known inputs. Timings are not a runtime benchmark. |
| Live SQL evaluation | Generated SQL and reference SQL both executed on BigQuery through `SQLExecutionPipeline` | Execution accuracy on the current dataset. |
| Live model evaluation | The full agent with the local Ollama model | End-to-end pass rate, grounding, latency, tokens. |

Canned fake query results are never presented as SQL accuracy. Scripted
1 ms LLM timings are never presented as latency.

"Unavailable" means the model provider or the warehouse could not be reached
or timed out (an `LLMProviderError`, a connection failure, or a query
timeout). Any other failure is an "error". Both are counted, separately,
and both stay in the attempted denominator.

## 3. Legacy scorers (historical comparator, unchanged)

The six deterministic scorers in `evaluation/scorers.py` are kept unchanged
for comparability. A case passes only when every scorer that ran passes.
The four downstream scorers run only when the run's status is `success`, so
their per-metric denominators shrink on failed, rejected or errored runs.
Every report states each metric's denominator beside its count.

| Scorer | Rule | Blind spots to disclose |
|---|---|---|
| `guardrail_pass` | status is not `rejected` | passes on status `error`; in Phase 7 a gate rejection at analysis time is also `rejected`, so this scorer is additionally reported split by terminal stage |
| `executed` | status is `success` | a successful SQL job is not a successful answer; a `success` run can end at the `execution` stage with no analysis at all |
| `table_grounding` | referenced tables equal the expected set | passes when the case sets no expectation |
| `execution_accuracy` | agent rows equal reference rows by value; column names and column order ignored | values are sorted **within each row**, so two columns swapped inside a row (profit for revenue) compare equal; column-to-metric binding is not checked. Cells are rounded half-even to the tolerance's number of decimal places and compared for equality, not within ± tolerance |
| `repair_efficiency` | repair attempts within the case budget | cannot fail while the case budget (2) is at least `MAX_SQL_REPAIR_ATTEMPTS` (2) |
| `analysis_grounding` | the recorded `ungrounded_numbers` and `contract_violations` lists are empty | see the list below |

`analysis_grounding` trusts the run record and recomputes nothing. These
behaviours of the legacy guard let number-free or wrong-entity answers pass
and are disclosed, not fixed, in Phase 1:

- only `DIRECT ANSWER`, `KEY INSIGHTS` and `SUPPORTING NUMBERS` are checked;
  figures in `LIMITATIONS` and `SUGGESTED FOLLOW-UP` are never checked;
- number-free prose passes; a derivable number attached to the wrong entity
  or metric passes;
- bare integers up to 12 with no unit, and a day number beside a month name,
  are skipped;
- any number in the question grounds a non-percentage figure, so a copied
  year passes; the row count is always grounded;
- hedged figures accept a wide band ("over 50%" accepts 50 to 75);
- the grounded set includes pairwise differences, contiguous subtotals and
  shares, so coincidental matches are common on integer data;
- only the first `MAX_ANALYSIS_ROWS` (50) rows ground the prose;
- `contract_violations` checks section headers and the absence of Markdown
  tables, not content;
- any zero-row result takes the deterministic empty-result path, which
  records empty violation lists and therefore passes, even on an answerable
  question whose SQL filtered everything out;
- a `success` run with no analysis metadata passes because both lists
  default to empty.

### Supplementary measures added in Phase 1 (not part of the legacy pass rate)

- `sql_accuracy_any_outcome`: the unchanged `score_execution_accuracy`
  function applied to the final SQL's rows on every run whose SQL execution
  succeeded, whatever happened to the analysis afterwards.
- `execution_coverage`: runs with a successful SQL execution / all attempted
  runs.

Both are reported for before and after. They are what makes SQL accuracy
visible when Phase 7 blocks an answer at analysis time.

## 4. Answer-level rubric (applies to both systems in Phase 7)

The before and after systems are scored on their actual rendered answers with
the same rubric and the same frozen facts. The old system is not required to
emit the new structured contract; contract compliance is reported separately
for the new system.

**Frozen facts.** `evaluation/golden_expected_facts.yaml` lists, per golden
case, the direct-answer figures (entity, metric, value, unit), the minimum
coverage, figures that are allowed but not required, and the expected outcome
of the empty-result case. The values were derived by executing the reference
SQL through `SQLExecutionPipeline` on the reconciled dataset, not by the
production model, gate or verifier. Phase 7 re-executes the committed
`executed_sql` and the reference SQL on a snapshot whose reconciliation
matches the before manifest when it needs result sets; result rows are not
stored in committed artifacts.

**Claim extraction.** A claim is any numeric figure in any of the five
sections of the rendered answer, including `LIMITATIONS` and
`SUGGESTED FOLLOW-UP`. Figures are found with the legacy guard's number
pattern and then judged by hand against the annex; the hand judgement is
recorded per claim in the comparison report. The same extractor and judge
apply to both systems.

**Precision rule.** A claim is correct when it equals the expected value
rounded to the number of decimal places the answer states, with a minimum of
2 decimal places for money and 1 for percentages; a figure stated more
coarsely than that minimum ("$24,800", "8%") is scored as imprecise and
counts as incorrect. A fraction and its percentage form are the same value; a
rank or a group name is correct when it matches the annex entity. A figure
that is not in the annex is **supported** when it is a documented derivation
of annex or reference-result values (a sum, difference, share, mean or count
over them, or a component the reference result contains, such as one month
of a two-month total) and is correct under the same rounding rule; otherwise
it is **unsupported**. The hand judge records the derivation used for every
supported claim. This rule does not depend on the Phase 2 registry.

**Answer success** on an answerable case requires all of:

1. relevant numerical coverage: the figures named by the case's
   `minimum_coverage` are present;
2. correct entity and metric attribution: each figure is attached to the
   group, entity and metric it belongs to;
3. correct value and scope: each stated figure satisfies the precision rule
   for the population and filters the question states.

Failing outcomes on an answerable case: number-free prose, irrelevant
numbers (a row count, a year copied from the question, a figure from another
metric), missing analysis, universal refusal, a block, an error,
unavailability, and a deterministic empty-result text on an answerable case.

**Definitions.**

- published answer: a run with status `success` whose rendered analysis was
  returned to the caller;
- unsafe published answer: a published answer on any case that contains at
  least one incorrect or unsupported claim under the precision rule, in any
  section;
- useful verified answer: a published answer that meets all three success
  criteria. For the before system "verified" means verified by this rubric;
  the before system has no verifier;
- claim precision: correct claims / published claims, pooled over all
  published answers (micro average). The per-answer mean is reported beside
  it. A zero-claim answer has no precision and is excluded from both.

**Empty-result case.** `no_sales_in_1999` is correct when status is
`success`, the row count is 0, the analysis was generated deterministically,
and no sales figure is stated. A model-written analysis containing any sales
figure for 1999 is an unsafe published answer.

**Reported measures**, each with its denominator written out: unsafe
published answers / attempted; useful verified answers / answerable; claim
precision (pooled and per-answer mean) / published claims; required-fact and
group coverage / answerable; blocks, errors and unavailable runs / attempted,
each separately; empty-result outcome / empty-result cases.

## 5. Trials, warm-up and cache state

- **Trial count: 3** full passes over the 15 golden cases per report. Every
  trial is published; the headline uses the pooled denominator of 45 case
  runs with the per-trial figures beside it. No best-of selection.
- **Warm-up:** one documented question is run through the agent inside the
  evaluation process before trial 1 (`--warm-up-question`). Its status,
  duration and run id are recorded in the manifest; it is excluded from the
  results. The model residency reported by Ollama is recorded before each
  trial.
- **Caches disclosed:** one `InsightsAgent` serves all three trials, so the
  schema cache (`SCHEMA_CACHE_TTL_SECONDS`, 300 s) is warm after the first
  case and the model stays resident; later trials are expected to be faster
  and are not independent cold runs. BigQuery's result cache is enabled for
  executions and disabled for dry runs; `cache_hit` is recorded per
  execution and reported in aggregate. Generation uses the configured
  temperature (0.1) and is not deterministic. Phase 7 must use the same cache
  settings, trial count and warm-up procedure.
- The guardrail suite runs once per report with no model involved.
- **Stop rule.** A report is not a valid before or after report when its
  manifest or any manifest section has an error, when any dataset
  reconciliation figure (numeric or column-level) does not match, or when
  the model was unavailable at warm-up. The runner refuses to write a report
  in those cases. The manifest and reconciliation stops can be overridden
  explicitly for diagnosis, in which case the report carries a warning line
  and an `invalid_baseline` flag in its JSON; the unavailable-model stop
  cannot be overridden.

## 6. Latency, cost and failure accounting

- Per-case `duration_ms` wraps `InsightsAgent.run` only, including the
  run-record write; the reference query is excluded. It is not the record's
  `runtime.total_duration_ms`, which spans `initialize_run` to
  `finalize_run` inside the graph.
- Mean is the arithmetic mean. `median` is `statistics.median`. `p50` and
  `p95` take the 0-based index `round(fraction * (n - 1))` with Python's
  round-half-to-even, clamped to the range, with no interpolation; this is
  not the nearest-rank method. For n = 15, p95 is the second-largest value.
- Per-node times come from the run record's `node_trace`, which covers the
  five work nodes; repeated `execute_sql`/`repair_sql` entries are separate
  samples. Per-role LLM time comes from `llm_calls` grouped by `purpose`
  (`sql_generation`, `sql_repair`, `result_analysis`); an empty-result run
  has no `result_analysis` call. Phase 7 adds its new nodes and roles
  without removing these.
- Per case the report records LLM call count, tokens, the final execution's
  bytes processed and billed and cache flag, and the number of SQL pipeline
  executions including repairs. In aggregate it records those final-execution
  bytes and cache hits, total executions, and counts of rejected, errored and
  unavailable runs. Per-execution values for repaired runs are in the
  evidence file's `sql_execution` records.
- Failed, blocked and unavailable runs keep their measured latency in the
  pooled figures.

## 7. Identity recorded in every manifest

Git head, branch, dirty files, a hash of the diff against HEAD and of each
untracked file; SHA-256 of each CSV and each case file; live table row
counts, sizes, location, partitioning, clustering and column schema (table
last-modified time is not exposed by the reader interface and is not
recorded); CSV-versus-BigQuery reconciliation: numeric aggregates per table and, for
every string and date column, the distinct-value count and a hash of the
sorted distinct values, each with a match flag; and, in the separate dataset
check artifact, a keyed row-level fingerprint per table (every column of
every row in key order, canonicalised identically on both sides) plus
foreign-key orphan counts, because distinct-value sets cannot detect swapped
row associations; the SHA-256 of the
expected-facts annex; Ollama server version, model tag, digest, family, parameter size and
quantisation; temperature, timeout and host; every cap in `src/config.py`;
run-record version, manifest version, registry version (none before Phase
2), package versions and the lockfile hash; Python version, platform, CPU,
memory and timestamp; trial count, warm-up record, run-log path and model
residency per trial. A dataset or model identity change between before and
after invalidates the paired comparison unless both sides are rerun on
matching snapshots.

## 8. Artifacts

| Artifact | Content | Committed |
|---|---|---|
| `evaluation/reports/latest.{md,json}` | historical 2026-08-25 report quoted by the README | preserved unchanged |
| `evaluation/reports/before_semantic_gate.{md,json}` | Phase 1 report with manifest, per-trial and pooled figures, supplementary measures | yes |
| `evaluation/reports/before_semantic_gate_dataset_check.json` | keyed row-level fingerprints and foreign-key counts, CSV versus live, from `python -m evaluation.dataset_check` | yes |
| `evaluation/reports/before_semantic_gate_runs.jsonl` | evidence extracted from the evaluation run log by `python -m evaluation.evidence`: the `graph_run` record of each of the 45 case runs plus their linked `sql_execution` records, with `case_id` and `trial` added and the project id redacted | yes, under plan.md's allowance for the bundled synthetic dataset |
| `evaluation/reports/after_semantic_gate.{md,json}`, `semantic_gate_comparison.md` | Phase 7 | later |

The evidence file is produced from a run log written to a scratch path, not
from the application's production log, and only the filtered, redacted
records are committed. It includes model-written analysis text and
model-written SQL, because those are what Phase 7 rescores. It excludes
prompts, `raw_model_output`, result rows, attack queries, reference queries,
manifest queries and the warm-up run. It is an evaluation artifact, distinct
from durable application logging, and is permitted for the bundled synthetic
dataset only. Credentials and production rows are never committed.

## 9. What Phase 1 does not measure

Phase 1 measures the unchanged system. It does not measure the correctness
of prose figures beyond derivability, entity or metric attribution, verifier
behaviour, or publication safety across sinks. Those are Phase 7 measures and
are applied retroactively to the committed baseline evidence under section 4.
SQL value accuracy is measured in Phase 1.

## 10. Frozen requirements for later phases

- **Challenge cohort** (plan.md evaluation item 2): before the Phase 7
  comparison, a separate cohort with hand-derived expected metrics, group
  identities, weighting, relevant numerical facts and minimum coverage per
  case, covering the minimum challenge list in plan.md. Its expectations are
  written before the gate exists and never generated by the gate or verifier.
- **Verifier ablation** (plan.md evaluation item 8): full verifier mode and
  deterministic-only mode are compared on the same frozen candidate contracts
  in an offline experiment that reports additional semantic detections, false
  approvals, false rejections and added latency. The production verifier
  never grades its own benchmark performance. The shadow mode is not a
  user-facing bypass.
- **Column binding** (section 3): Phase 7 adds a column-to-metric binding
  check beside the unchanged `execution_accuracy`, using re-executed result
  sets.

## 11. Reproduction

```bash
uv run pytest tests/ -q                                               # offline
GCP_PROJECT_ID=<project> uv run python -m evaluation.run_evaluation --guardrails-only
GCP_PROJECT_ID=<project> uv run python -m evaluation.run_evaluation \
  --trials 3 --report-name before_semantic_gate \
  --warm-up-question "How many products are in the catalogue?" \
  --run-log <scratch>/before_runs.jsonl \
  --notes "<free text about the run>"
uv run python -m evaluation.evidence --runs <scratch>/before_runs.jsonl \
  --report evaluation/reports/before_semantic_gate.json \
  --out evaluation/reports/before_semantic_gate_runs.jsonl
GCP_PROJECT_ID=<project> uv run python -m evaluation.dataset_check \
  --out evaluation/reports/before_semantic_gate_dataset_check.json
```

The dataset check is the keyed row-level comparison (section 7) and must
report `all_match: true` on the same data the report was measured on; the
evidence export exits non-zero when any graph run or linked SQL execution
named by the report is missing from the run log.

The guardrail suite runs once per invocation, which with in-process trials is
once per report.
