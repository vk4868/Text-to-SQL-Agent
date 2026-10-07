# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Execution rules and approval gates

Read `plan.md` and `NOTES.md` before feature work. The verified-numerical-answers feature **stopped after Phase 4 by the user's decision on 2026-10-07**. Do not resume Phases 5–7 without explicit approval. Documentation maintenance does not authorise implementation or live evaluation. Distinguish this feature's phases from the original project's historical phases.

- Fable 5.1 orchestrates and integrates; Sonnet handles small prescribed changes and repetitive work; Opus handles reasoning-intensive implementation and independent review. Record actual agent use; do not claim unavailable models or reviews occurred.
- Follow the phase deliverables, implementation matrix, tests and exit evidence in `plan.md`. Present completion evidence and stop for the user's approval before starting the next phase. Report discrepancies and unresolved gaps; a design review is not runtime validation.
- The interview target is about two hours, with a three-hour hard stop. Record elapsed work and unfinished next steps; do not silently reset or extend the timebox. Preserve the actual branch/PR history: no squashing or tidying commits.
- Preserve the invariants below. Database reloads, destructive operations and other mutations require authorisation covering that operation; existing explicit authorisation remains valid. A separately authorised administrative reload must never become a model-accessible write path.
- Never commit secrets, credentials, `.env` contents or real personal data. Keep raw production rows and logs out of the repository. Only intentionally selected synthetic evaluation evidence belongs in reports; inspect it for secrets, personal data and project-id redaction before committing.

## Commands

Python 3.11 via `uv`. The system Python is 3.14 and has none of the deps — **always** run through `uv run` or `.venv/bin/python`.

```bash
uv sync                                   # install deps from uv.lock
gcloud auth application-default login     # BigQuery auth (ADC; no service-account key in repo)
ollama serve                              # local LLM, model tag from OLLAMA_MODEL

uv run pytest tests/ -q                   # the real suite: hermetic, no creds, no Ollama
uv run pytest tests/unit/graph -q         # one area
uv run pytest tests/unit/semantics -q      # semantic registry, query compiler, contracts, gate
uv run pytest tests/unit/test_cli.py::TestExitCodes::test_success_exits_zero -q

uv run python main.py "What were total net sales by month in 2025?"
uv run python main.py "..." --trace       # add per-node timings
uv run python main.py "..." --json        # the full run record
uv run python main.py "..." --sql-only    # just the SQL
uv run python main.py "..." --max-rows 25 # rows to print (default 10)
uv run streamlit run app.py               # web UI over the same InsightsAgent

uv run python -m evaluation.run_evaluation --guardrails-only   # 19 adversarial cases; no LLM, ~1s
uv run python -m evaluation.run_evaluation                     # 15 golden cases; live BigQuery + Ollama, ~5 min
uv run python -m evaluation.run_evaluation --fail-under 0.7    # exit 1 on regression
uv run python -m evaluation.run_evaluation --case net_sales_by_month_2025 --no-write
# Requires GCP_PROJECT_ID to be configured for the actual project.
# Evidence inputs must come from the same evaluation run; replace these example paths.
uv run python -m evaluation.evidence --runs /tmp/konkrd-runs.jsonl --report /tmp/konkrd-report.json --out /tmp/konkrd-evidence.jsonl
uv run python -m evaluation.dataset_check --out /tmp/konkrd-dataset-check.json

uv run python scripts/demo.py --guardrails-only   # the model-free demo
uv run python scripts/report_runs.py --last 20 --failures   # summarise logs/runs.jsonl

uvx pyrefly check                         # type checker configured in pyproject.toml
```

Exit codes: the CLI returns 0 on success, 1 for a failed **or rejected** run, 2 for usage; the evaluation returns 0, 1 (below `--fail-under`), 2 (harness error).

`uvx pyrefly check` is **not clean today** — it reports 2 errors: one in `tests/unit/graph/test_graph_invariants.py`, where a test passes a plain dict to a function typed `AgentState`, and one in `tests/fakes/bigquery.py` (a `BaseException` return annotation). Don't treat those as a regression you introduced; don't go fixing them unless asked.

CI (`.github/workflows/tests.yml`) installs with `pip install -r requirements.txt`, not from `uv.lock`. Adding a dependency means adding it to **both** `pyproject.toml` (then `uv lock`) and `requirements.txt`, or CI will fail on import.

**Two kinds of test live here, and they are not the same thing:**

- `tests/` is a real pytest suite. Fully hermetic — autouse fixtures make constructing a live `bigquery.Client` or `ollama.Client` raise, and redirect logs into `tmp_path`. It must pass with credentials removed and Ollama unreachable.
- `scripts/smoke/smoke_*.py` are print-only live demos, **run as modules** so the repo root is on the path:

  ```bash
  uv run python -m scripts.smoke.smoke_insights_graph
  ```

  Running them by path fails with `ModuleNotFoundError: No module named 'src'`. They are named `smoke_*` rather than `test_*` precisely so pytest can never collect them; `[tool.pytest.ini_options] testpaths = ["tests"]` is a second guard, and it is load-bearing — several make live BigQuery calls at import.

Live smoke scripts get their collaborators from `scripts/smoke/_wiring.py::build_live_nodes()`. Never construct `InsightsGraphNodes` inline in a script: all five collaborators are keyword-only and required, and six scripts once passed subsets and died with `TypeError` before reaching what they meant to test.

Markers `live_bq` and `live_llm` are registered and deselected by default.

**Writing tests.** `tests/conftest.py` provides `fake_bq` (a `FakeBigQueryService`), `execution_pipeline`, `schema_provider`, `make_scripted_llm` and `run_log_path`. `tests/fakes/factories.py::make_nodes` is the test-side twin of `build_live_nodes`: it wires all five collaborators over fakes and lets you pass a separate `ScriptedLLMClient` per role (`generation_llm`, `repair_llm`, `analysis_llm`) so each has its own response queue. `FakeBigQueryService` implements the `BigQueryReader` Protocol rather than subclassing `BigQueryService`, whose `__init__` builds a live client; it records `dry_run_calls` and `run_query_calls` so tests can assert a guardrail stopped short. `WELL_FORMED_ANALYSIS` in the factories module is a five-section analysis that passes the contract.

**This file is under test.** `tests/unit/test_docs_claims.py` parses `CLAUDE.md` along with the README, PIPELINE.md and `docs/`. Any backticked path under `src/`, `tests/`, `scripts/`, `evaluation/`, `Datasets/` or `docs/` must exist; any `python -m` module must be importable; a test count must be a floor like "400+ tests", never an exact number; and any claim that the guardrails keep a hostile query from reaching BigQuery *at all* is rejected, because stage 2 makes a `list_tables` catalog call — the defensible phrasing is "before a byte of table data is scanned". Run that test file after editing any doc.

## Architecture

Natural-language question → live schema grounding → local Gemma writes SQL → 6-stage deterministic guardrail pipeline → read-only execution → LLM turns rows into a written business analysis. Over a small synthetic star schema (`business_insights`: `fact_sales`, `dim_customers`, `dim_products`).

**The load-bearing idea:** the LLM is untrusted and only ever emits *text*. Deterministic code must prove that text safe before a byte is scanned. Prompts *ask* for safe SQL; the guardrails *enforce* it. Never add a path where model output reaches BigQuery without going through `SQLExecutionPipeline.execute()`.

### The guardrail pipeline — `src/sql_execution_pipeline.py`

Six stages, each timed, each able to reject early. Validators in `src/sql_validator.py` use `sqlglot` (read dialect `"bigquery"`).

1. `validate_read_only_sql` — parseable, exactly one statement, must be an `exp.Query`.
2. `validate_table_access` — AST walk; every physical table fully-qualified and on the live allowlist. CTE names collected first and skipped.
3. `enforce_result_limit` — injects `LIMIT`, reduces one that's too high, **rejects** a parameterised one. Its output becomes `executed_sql`.
4. `dry_run_query` — real BigQuery validation + byte estimate, no billing. Where hallucinated columns die.
5. Cost check — estimate vs `MAX_QUERY_BYTES`.
6. `run_query` — read-only, `maximum_bytes_billed`, `job_timeout_ms`, `max_results`. A client-side result timeout requests job cancellation and records `cancel_requested`; this does not prove the remote job finished cancelling.

Stages 5/6 and 3/6 deliberately double up (estimate *and* hard billing cap; injected LIMIT *and* client-side cap).

### LangGraph is the only orchestrator — `src/graph/`

`src/agent.py::InsightsAgent` is the sole entry point; `src/cli.py` and `main.py` sit on top of it. The imperative `QuestionToSQLPipeline` / `QuestionToInsightsPipeline` were retired in the original project's Phase 7 — they duplicated the graph's control flow. They exist only in git history.

`build_insights_graph` (`builder.py`) is the production path:

```
START → initialize_run →(question ok?)→ get_schema →(ok?)→ generate_sql →(ok?)→ execute_sql
                                                                                    │
                                          ┌──── repair ────┐                        │
                                          ↓                │                        ↓
                                     repair_sql ───────────┘                  analyze_result
                                          │                                         │
                                          └──────────→ finalize_run ←───────────────┘ → END
```

Four smaller builders (`build_schema_graph`, `build_sql_generation_graph`, `build_sql_execution_graph`, `build_sql_repair_graph`) are the incremental slices the workflow was grown through. They end at `END` and emit **no** run record — that is deliberate, not an oversight. Keep them working.

`app.py` (Streamlit) is presentation only: it builds one `InsightsAgent` under `st.cache_resource`, runs it only on an explicit ask, caches the result in session state, and renders everything from the run record. A test asserts the file never names `build_insights_graph` or `SQLExecutionPipeline`; keep logic out of it. `tests/unit/test_streamlit_app.py` drives it end to end with Streamlit's `AppTest`.

### Conventions that matter

- **Accumulators are reducers.** `node_trace`, `repair_history`, `llm_calls`, `sql_execution_run_ids` are `Annotated[list, operator.add]` in `state.py`. A node returns **only its new entries**. Returning the whole list appends it to what's there and duplicates all prior history — the single easiest way to break this graph.
- **`finalize_run` is the graph's only exit.** Every terminal path routes there, so a run is classified, logged and recorded exactly once. Adding a new terminal path means routing it to `finalize_run`, never to `END`. A test reads the builder's AST to enforce this.
- **`InsightsAgent.run` never raises.** Nodes convert *expected* failures into routed state (`error_stage` + `error_message`); anything unexpected escapes the graph, so the agent catches it at the outermost boundary, logs the traceback, and writes a crash record. This is what keeps "every run leaves exactly one record" true.
- **Structured dicts, never exceptions, across boundaries.** Every exit carries `{status, stage, error_type, message, ...}`. `status` is `success` / `rejected` (a guardrail refused it) / `error` (something broke). **`rejected` is not `error`** — a refused query means the system worked, and the evaluation harness depends on telling them apart.
- **Success requires positive evidence.** `_classify_outcome` claims success only when `execution_result["status"] == "success"`, never from the mere presence of a result. Inferring it caused a failed run to report success with exit code 0.
- **`timed_node` wraps the five work nodes**, not the two lifecycle nodes — `finalize_run` cannot appear in the trace it assembles. It records every exit path including early precondition returns, and re-raises unexpected exceptions with the node name attached.
- **Repair policy lives in exactly one place** — `src/repair_policy.py`. Only `validation`, `table_access`, `dry_run` are repairable; a cost or timeout failure means retrying wastes money. `route_after_execution` delegates to it, and a test asserts the delegation actually happens.
- **The LLM is behind one Protocol** — `LLMClient.generate_response(prompt) -> LLMResponse`. `OllamaGemmaClient` maps provider errors to `LLMProviderError`. `ScriptedLLMClient` (`src/llm/scripted.py`) returns queued canned responses and is what makes the suite hermetic; `MockLLMClient` returns fixed prose and cannot drive SQL tests.
- **Config resolves at call time**, never as import-time default arguments — otherwise tests cannot redirect the run log or the caps. `src/config.py` calls `load_dotenv()` first, since every setting is a module constant read at import.
- **No I/O at import.** `src/tools.py` exposes `build_tools()` / `get_default_tools()`; `BigQueryService` takes an injected `client=`. A test walks every `src` module in a subprocess with both clients stubbed to prove it.
- **Schema is always live** but cached behind `SCHEMA_CACHE_TTL_SECONDS` (default 300) — it costs ~4 API calls per table and is requested again on every repair attempt. `SchemaProvider.fetch_count` exists so tests can assert the cache works.
- **Distinguish prompt guidance from enforcement.** `prompts/sql_generation.py` has 14 rules. Fully-qualified physical table names and a single read-only statement are enforced by validators. Avoiding `SELECT *` is prompt guidance only: the validators do not reject wildcards. Keep prompts aligned with guardrails without claiming every prompt rule is enforced.
- Business semantics in the prompt: "sales"/"revenue" → `net_revenue`, "profit" → `profit_amount`, filter `sale_date` for date ranges (partition pruning). `Datasets/DATA_DICTIONARY.md` has the column formulas.
- **Comment every code block.** Every function, dataclass and distinct block inside a function states what it does, the inputs it requires and what it returns, including the rejection reason codes it can produce. `src/analysis_validation.py` is the reference example; keep the practice for every new module.

### Evaluation — `evaluation/`

Deterministic, no LLM judge. `evaluation/cases.py` loads and validates two YAML files on read: `evaluation/golden_cases.yaml` (15 questions, each with hand-written reference SQL, expected tables, a `comparison` of `multiset` / `ordered` / `scalar` / `none`) and `evaluation/attack_cases.yaml` (19 hostile queries, each with the `status` and `stage` expected to refuse it). `evaluation/harness.py` runs a golden case through `InsightsAgent` and runs the reference SQL through the **same** `SQLExecutionPipeline`, so both sides get the same row cap and normalisation. `evaluation/scorers.py` has six scorers; a case passes only when all six do, and `execution_accuracy` compares result sets by value, ignoring column names and order.

`evaluation/report.py` writes `evaluation/reports/latest.json` and `latest.md`, which are **committed**; timestamped `report-*` files are gitignored. The README's pass rate, per-metric counts and adversarial count are asserted against `latest.json` and `attack_cases.yaml` by the docs-claims test, so re-running the live evaluation and committing a new `latest.json` means updating the README figures in the same change. Adding an attack case changes the "19/19" quoted in the README and `docs/PORTFOLIO.md`.

**Phase 1 tooling.** `evaluation/PROTOCOL.md` is the frozen evaluation protocol and `evaluation/golden_expected_facts.yaml` holds the expected facts per golden case. `run_evaluation` also takes `--trials`, `--report-name` (writes a named report and never touches `latest.*`), `--warm-up-question`, `--run-log`, `--notes`, `--overwrite` and `--allow-manifest-errors`. `uv run python -m evaluation.evidence` and `uv run python -m evaluation.dataset_check` export evidence and check the dataset. The committed before report is `evaluation/reports/before_semantic_gate.md` / `.json`, with `evaluation/reports/before_semantic_gate_runs.jsonl` and `evaluation/reports/before_semantic_gate_dataset_check.json`. `latest.*` remains the historical report the README quotes. The project id placeholder is substituted at load and redacted on write.

The evidence exporter requires `--runs`, `--report` and `--out`; the dataset checker requires `--out` (complete examples above). Configure the actual project via `GCP_PROJECT_ID`; a redacted placeholder is not a runnable project. All application, reference and evidence SQL must pass through `SQLExecutionPipeline`. Preserve frozen before artifacts and the historical `latest.*`; use fresh named reports for later runs. `--allow-manifest-errors` does not turn failed reconciliation into a valid baseline. Compare before/after on the frozen data, model and trials, reporting grounding, runtime and SQL accuracy with explicit denominators and unavailable cases. Count blocked/clarified answers separately from successful numerical answers so abstention cannot inflate accuracy.

### Observability

Two record types in `logs/runs.jsonl`, linked by id:
- `event: "sql_execution"` — one per `SQLExecutionPipeline.execute()`, with `run_id` and per-stage timings.
- `event: "graph_run"` — one per agent run, built by `src/run_record.py::build_graph_run_record` (a pure function over final state, versioned via `RUN_RECORD_VERSION`). Sections: `governance` (the caps in force), `outcome`, `sql`, `data`, `cost`, `runtime` (node trace + linked `sql_execution_run_ids`), `tokens`, `analysis`.

`run_id` ⊂ `graph_run_id`. Raw prompts, raw model output and result rows are **deliberately excluded** from the durable record; rows go in the return value only.

### Checking the prose

`src/analysis_guard.py` is the legacy diagnostic guard. It checks whether figures in the checked sections occur in the rows, are a bounded derivation of them (column aggregate, contiguous subtotal, difference, share of total, fraction-as-percentage, row count), or are numbers the **question** contained. Failures currently attach warnings; the live application still publishes the analysis. This is not a fail-closed publication gate.

Two rules keep it honest, and both have tests:

- **The generated SQL is never passed as grounding context.** It is the same untrusted output being checked, so a model could otherwise launder a fabrication through its own `WHERE` clause.
- **Only claim-bearing sections are checked.** `LIMITATIONS` and `SUGGESTED FOLLOW-UP` are asked by the prompt to discuss data outside the result, so figures there are proposals.

It checks values, not attributions — see the README's limitations section before extending it.

### Semantic verification layer (feature branch, Phases 2-4)

Status: built and unit-tested on branch `feature/verified-numerical-answers`; **not yet wired** into the LangGraph graph, prompts, CLI or Streamlit (that is Phase 6). The bounded verifier (Phase 5) and the after-evaluation (Phase 7) are not started. The production path described above is unchanged.

- `src/semantics/metrics.yaml` + `src/semantics/registry.py` — versioned metric registry: 13 base measures, derived metrics with typed calculations, dimensions, joins, ambiguous phrases with a clarify policy, and a validator. Decisions are logged in `src/semantics/DECISIONS.md`.
- `src/semantic_query.py` — strict query-intent parser that forces clarification on ambiguous phrases; bounded compiler that renders SQL only from registry calculations and joins for scalar / single-dimension / month-year / top-N shapes; engine-owned bindings; result manifest with executed-SQL verification and completeness.
- `src/analysis_contracts.py` — the answer contract the analysis model must emit, with a reference-only strict parser.
- `src/analysis_validation.py` — the deterministic gate: Decimal recomputation, ROUND_HALF_UP at registry precision, entity / metric / unit / scope checks, positive coverage, and whole-answer rejection with stable reason codes.

Trust rule: the model only proposes. Metric ids, result ids, row refs, completeness and registry versions are engine-owned, and a verifier (Phase 5) can never override a deterministic rejection. Tests live under `tests/unit/semantics/`.

**Standalone flow:** proposed query intent → registry validation and ambiguity handling → bounded SQL compiler → `SQLExecutionPipeline` → engine-owned result binding/manifest → answer-contract parsing → deterministic gate. These components are not yet an integrated application path. The planned continuation is a bounded verifier followed by trusted rendering/publication through the graph; both are unfinished. On resumption, a deterministic rejection must block the whole proposed answer before any user-facing draft appears, and must not be sent to a verifier for an override.

**Required integration contract:** `validate_answer(..., question_metric_ids=...)` requires metric ids from the independently validated query intent. Never derive this argument from the answer model's self-declared metrics. An answerable numerical question requires relevant numerical evidence; number-free prose or unrelated numbers must not count as a pass. Preserve explicit clarification, rejection and empty-result outcomes rather than inventing numbers to satisfy coverage.

**Current bounds and known gaps:**

- The compiler covers 14 of 15 golden question shapes; average discount requires clarification. Group averages, shares, percentage changes and multi-dimension grouping remain unsupported compiler shapes. Gate operations are not a promise that the compiler supports the corresponding question.
- Complete grouped results require coverage of every row only up to 12 rows. Above that bound, the gate does not establish full group coverage.
- Subtotal detection relies on recognised labels (`total`, `grand total`, `subtotal`, `all`, `overall`) and null dimension values; it does not recognise arbitrary subtotal wording.
- Free-text contract fields can contain digits and must not be displayed as verified prose. Trusted rendering and checks over every user-visible numerical channel remain Phase 6 work.
- Verification establishes consistency with supplied results and approved definitions within these bounds. It does not prove source-data correctness or the right interpretation of an ambiguous question. Standalone unit tests do not prove end-to-end publication safety.

### Documentation

`tests/unit/test_docs_claims.py` asserts every path and `python -m` command in the docs exists, and that no doc describes a deleted component as current. Docs went stale once; that test is why they should not again.

`dataset_reference.md` at the repo root describes an earlier exploration of the public `thelook_ecommerce` dataset. It is **not** what the code queries and is not checked by the docs test; the live dataset is `Datasets/` and its `Datasets/DATA_DICTIONARY.md`.

### Current state

**Historical project:** original Phases 0 and 7–10 were completed, followed by the Streamlit front end. The historical `latest.*` report records 11/15 overall (73%), 14/15 SQL execution accuracy, 12/15 legacy analysis grounding and 19/19 adversarial queries refused. It predates the Australian-data baseline and must not be presented as that baseline.

**Current feature:** Phases 1–4 are committed on `feature/verified-numerical-answers`; implementation stopped after Phase 4. The current-data before report records 31/45 overall across three trials of 15 questions (9/15, 11/15, 11/15), 42/45 SQL execution accuracy, 33/45 legacy analysis grounding, 19/19 adversarial queries refused, and latency mean 19.2 s / p95 26.5 s. The repository has 800+ hermetic tests. Phases 5–7 are not started; there is no after report or measured end-to-end improvement from the new layer. The live application still uses the warning-only prose guard.
