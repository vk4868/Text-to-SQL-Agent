# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Python 3.11 via `uv`. The system Python is 3.14 and has none of the deps — **always** run through `uv run` or `.venv/bin/python`.

```bash
uv sync                                   # install deps from uv.lock
gcloud auth application-default login     # BigQuery auth (ADC; no service-account key in repo)
ollama serve                              # local LLM, model tag from OLLAMA_MODEL

uv run pytest tests/ -q                   # the real suite: hermetic, no creds, no Ollama
uv run pytest tests/unit/graph -q         # one area
uv run pytest tests/unit/test_cli.py::TestExitCodes::test_success_exits_zero -q

uv run python main.py "What were total net sales by month in 2025?"
uv run python main.py "..." --trace       # add per-node timings
uv run python main.py "..." --json        # the full run record
uv run python main.py "..." --sql-only    # just the SQL
```

**Two kinds of test live here, and they are not the same thing:**

- `tests/` is a real pytest suite. Fully hermetic — autouse fixtures make constructing a live `bigquery.Client` or `ollama.Client` raise, and redirect logs into `tmp_path`. It must pass with credentials removed and Ollama unreachable.
- `scripts/smoke/smoke_*.py` are print-only live demos, **run as modules** so the repo root is on the path:

  ```bash
  uv run python -m scripts.smoke.smoke_insights_graph
  ```

  Running them by path fails with `ModuleNotFoundError: No module named 'src'`. They are named `smoke_*` rather than `test_*` precisely so pytest can never collect them; `[tool.pytest.ini_options] testpaths = ["tests"]` is a second guard, and it is load-bearing — several make live BigQuery calls at import.

Live smoke scripts get their collaborators from `scripts/smoke/_wiring.py::build_live_nodes()`. Never construct `InsightsGraphNodes` inline in a script: all five collaborators are keyword-only and required, and six scripts once passed subsets and died with `TypeError` before reaching what they meant to test.

Markers `live_bq` and `live_llm` are registered and deselected by default.

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
6. `run_query` — read-only, `maximum_bytes_billed`, `job_timeout_ms` (timeout cancels the job), `max_results`.

Stages 5/6 and 3/6 deliberately double up (estimate *and* hard billing cap; injected LIMIT *and* client-side cap).

### LangGraph is the only orchestrator — `src/graph/`

`src/agent.py::InsightsAgent` is the sole entry point; `src/cli.py` and `main.py` sit on top of it. The imperative `QuestionToSQLPipeline` / `QuestionToInsightsPipeline` were retired in Phase 7 — they duplicated the graph's control flow. They exist only in git history.

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
- **Prompt rules mirror guardrails.** `prompts/sql_generation.py` has 14 rules; those about fully-qualified names, single read-only statement and no `SELECT *` exist because stages 1–3 will otherwise reject. Changing a guardrail usually means changing the matching prompt rule.
- Business semantics in the prompt: "sales"/"revenue" → `net_revenue`, "profit" → `profit_amount`, filter `sale_date` for date ranges (partition pruning). `Datasets/DATA_DICTIONARY.md` has the column formulas.

### Observability

Two record types in `logs/runs.jsonl`, linked by id:
- `event: "sql_execution"` — one per `SQLExecutionPipeline.execute()`, with `run_id` and per-stage timings.
- `event: "graph_run"` — one per agent run, built by `src/run_record.py::build_graph_run_record` (a pure function over final state, versioned via `RUN_RECORD_VERSION`). Sections: `governance` (the caps in force), `outcome`, `sql`, `data`, `cost`, `runtime` (node trace + linked `sql_execution_run_ids`), `tokens`, `analysis`.

`run_id` ⊂ `graph_run_id`. Raw prompts, raw model output and result rows are **deliberately excluded** from the durable record; rows go in the return value only.

### Checking the prose

`src/analysis_guard.py` is the other half of the untrusted-LLM principle: every figure in the written analysis must be present in the rows, a bounded derivation of them (column aggregate, contiguous subtotal, difference, share of total, fraction-as-percentage, row count), or a number the **question** contained.

Two rules keep it honest, and both have tests:

- **The generated SQL is never passed as grounding context.** It is the same untrusted output being checked, so a model could otherwise launder a fabrication through its own `WHERE` clause.
- **Only claim-bearing sections are checked.** `LIMITATIONS` and `SUGGESTED FOLLOW-UP` are asked by the prompt to discuss data outside the result, so figures there are proposals.

It checks values, not attributions — see the README's limitations section before extending it.

### Documentation

`tests/unit/test_docs_claims.py` asserts every path and `python -m` command in the docs exists, and that no doc describes a deleted component as current. Docs went stale once; that test is why they should not again.

### Current state

Phases 0 and 7–10 complete, each independently verified by an audit that mutation-tested the suite. Evaluation: 11/15 (73%), 19/19 adversarial queries refused, 400+ hermetic tests.
