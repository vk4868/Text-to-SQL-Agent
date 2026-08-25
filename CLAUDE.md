# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Python 3.11 via `uv`. The system Python is 3.14 and has none of the deps — **always** run through `uv run` or `.venv/bin/python`.

```bash
uv sync                                   # install deps from uv.lock
gcloud auth application-default login     # BigQuery auth (ADC; no service-account key in repo)
ollama serve && ollama pull <OLLAMA_MODEL># local LLM must be running for any LLM-touching script

uv run python test_sql_validator.py       # run one "test"
```

There is **no pytest, no ruff, no lint step installed** (`.ruff_cache/` is a leftover). The ~23 root-level `test_*.py` files are *manual smoke scripts*: each has a `main()` that prints output to eyeball — no asserts, no test runner. Run them individually.

Which scripts need what:

| Need | Scripts |
|---|---|
| Nothing (pure logic) | `test_early_stage_routes.py`, `test_agent_state.py`, `test_schema_formatter.py` |
| BigQuery creds only | `test_bigquery_service.py`, `test_schema*.py`, `test_run_query.py`, `test_*_guardrail.py`, `test_sql_execution_pipeline.py`, `test_stage_timings.py`, `test_pipeline_observability.py` |
| Ollama only | `test_llm_interface.py`, `test_ollama_gemma_client.py`, `test_sql_generator.py`, `test_sql_repairer.py` |
| Both (end-to-end) | `test_question_to_sql_pipeline.py`, `test_question_to_insights_pipeline.py`, `test_result_analyzer.py`, `test_insights_graph.py`, `test_graph_automatic_repair.py`, `test_sql_*_graph.py` |

Config comes from `src/config.py`, every value env-overridable, `.env` loaded via `python-dotenv`. `.gitignore` excludes `*.json`, `.env`, `logs/` — do not try to commit credentials or run logs.

## Architecture

Natural-language business question → BigQuery SQL → guarded execution → written analysis, over a small synthetic star schema (`business_insights`: `fact_sales`, `dim_customers`, `dim_products`).

**The load-bearing idea:** the LLM is untrusted and only ever emits *text*. Deterministic code must prove that text safe before a byte is scanned. Prompts *ask* for safe SQL; the guardrails *enforce* it regardless of what comes back. Never add a path where model output reaches BigQuery without going through `SQLExecutionPipeline.execute()`.

### The guardrail pipeline — `src/sql_execution_pipeline.py`

Six stages, each timed, each able to reject early. Validators live in `src/sql_validator.py` and use `sqlglot` (read dialect `"bigquery"`).

1. `validate_read_only_sql` — parseable, exactly one statement, must be an `exp.Query` (SELECT/CTE). Returns normalized SQL used by all later stages.
2. `validate_table_access` — AST walk over `exp.Table`; every physical table must be fully-qualified `project.dataset.table` and on the live allowlist. CTE names are collected first and skipped.
3. `enforce_result_limit` — injects `LIMIT MAX_RESULT_ROWS`, reduces one that's too high, **rejects** a non-integer/parameterised LIMIT. Its output becomes `executed_sql`.
4. `BigQueryService.dry_run_query` — real BigQuery validation + byte estimate, no billing. This is where hallucinated columns die.
5. Cost check — estimate vs `MAX_QUERY_BYTES`.
6. `BigQueryService.run_query` — read-only, `maximum_bytes_billed`, `job_timeout_ms` (timeout cancels the job and raises `QueryExecutionTimeoutError`), `max_results` as a second belt over stage 3.

Stages 5/6 and 3/6 deliberately double up (estimate *and* hard billing cap; injected LIMIT *and* client-side cap).

### Two orchestrators over the same components

Both wire up the same pieces (`SchemaProvider`, `SQLGenerator`, `SQLExecutionPipeline`, `SQLRepairer`, `ResultAnalyzer`) — pick the layer a change belongs to and keep them consistent:

- **Imperative:** `QuestionToSQLPipeline` (question → schema → SQL → execute, with an in-loop repair retry) wrapped by `QuestionToInsightsPipeline` (adds analysis).
- **LangGraph:** `src/graph/` — `AgentState` (`state.py`, a `total=False` TypedDict), node methods on `InsightsGraphNodes` (`nodes.py`), and five progressively larger graph builders in `builder.py` (`build_schema_graph` → `build_sql_generation_graph` → `build_sql_execution_graph` → `build_sql_repair_graph` → `build_insights_graph`). The smaller builders exist as incremental test targets; keep them working when editing nodes.

Repair loop (both layers): only failures at stages `validation`, `table_access`, `dry_run` are repairable; execution/timeout/cost failures terminate. Bounded by `MAX_SQL_REPAIR_ATTEMPTS` (default 2). `SQLRepairer` feeds the failure stage + error message back to the LLM via `prompts/sql_repair.py`.

### Conventions that matter

- **Structured dicts, never exceptions, across boundaries.** Every pipeline exit returns `{status, stage, error_type, message, ...}` — `status` is one of `success` / `rejected` (guardrail said no) / `error` (something broke). Callers branch on `status` and `stage` strings; graph routers (`route_after_*`) do the same. Don't let a new failure mode escape as a raw exception.
- **Every return routes through `_finalize_result`**, which stamps the run id + duration and (in `SQLExecutionPipeline`) logs and appends a JSONL record. Adding an early return means adding it *through* the finalizer.
- **Nested run ids:** `run_id` (execution) ⊂ `question_run_id` ⊂ `insights_run_id` ⊂ `graph_run_id`. Timings accumulate in `stage_timings_ms` (pipeline) and `node_trace` (graph).
- **The LLM is behind one Protocol** — `LLMClient.generate_response(prompt) -> LLMResponse` (`src/llm/base.py`). `OllamaGemmaClient` maps `ollama.ResponseError` and `httpx.HTTPError` to a single `LLMProviderError` so callers catch one type. `MockLLMClient` exists to exercise pipelines with no model.
- **Constructors are keyword-only** (`*` in every `__init__`) and validate their caps/limits. Imports are absolute `from src.x import y` — scripts are run from the repo root.
- **Schema is always live**, never hardcoded: `SchemaProvider.get_schema_document()` pulls real BigQuery metadata via `BigQueryService`, formats it with `schema_formatter`, then appends the hand-written `RELATIONSHIPS` from `schema_config.py` (BigQuery exposes no foreign keys, so join hints must be told).
- **Prompt rules mirror guardrails.** `prompts/sql_generation.py` carries 14 rules; the ones about fully-qualified names, single read-only statement, and no `SELECT *` exist because stages 1–3 will otherwise reject. `prompts/result_analysis.py` forces a fixed output structure and forbids inventing numbers. Changing a guardrail usually means changing the matching prompt rule.
- Business semantics encoded in the prompt: "sales"/"revenue" → `net_revenue`, "profit" → `profit_amount`, filter `sale_date` for date ranges (partition pruning). `Datasets/DATA_DICTIONARY.md` has the full column formulas.

### Current state / traps

- **`src/graph/nodes.py` does not compile** — line 164 has an unclosed `return self._record_node_execution(` followed by a second `return` inside `generate_sql_node`: a half-finished refactor to record per-node timings via `_record_node_execution`. Every graph test fails on import until this is finished. The same file has three stray duplicate `from cryptography.hazmat.primitives import constant_time` imports at the top that should be deleted.
- `README.md` and `PIPELINE.md` are detailed but **stale**: they state LangGraph is unused and that analysis isn't folded into an orchestrator. Both are now built (`src/graph/`, `QuestionToInsightsPipeline`). Treat them as the conceptual guide (guardrail semantics, dataset model, failure shapes) and the code as truth for wiring. Update them when the graph work lands.
- `OLLAMA_MODEL` defaults to `gemma4:latest` — verify the tag exists in `ollama list`; a bad tag surfaces as `LLMProviderError`.
- `dataset_reference.md` documents `thelook_ecommerce`, an alternate public dataset that is **not** loaded — exploratory notes only.
- `src/tools.py` builds module-level singletons (`BigQueryService()` etc.) at import time, so importing it requires working credentials. It also once caused a circular import with `schema_provider` — keep the dependency one-way.
