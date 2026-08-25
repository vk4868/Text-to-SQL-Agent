# SQL BigQuery Agent

A schema-aware, text-to-SQL business-insights agent on top of Google BigQuery. You ask a business question in plain English; the agent grounds itself in the **live** BigQuery schema, asks a local LLM (Gemma via Ollama) to write BigQuery SQL, pushes that SQL through a **6-stage safety pipeline** before anything runs, executes it read-only under strict cost/row/time caps, and (separately) has the LLM turn the rows into a written business analysis. Every run is logged and recorded as structured JSONL.

This README is my own reference. For a line-by-line walkthrough of a single request from question to answer, see **[PIPELINE.md](PIPELINE.md)** — this file is the map, that file is the guided tour.

---

## 0. Status at a glance

| Layer | State |
|---|---|
| BigQuery data access (`BigQueryService`) | ✅ built + verified live |
| Schema → LLM document (`SchemaProvider`, `schema_formatter`) | ✅ |
| SQL guardrails (`sql_validator`: read-only, table-access, result-limit) | ✅ |
| Safe execution pipeline (`SQLExecutionPipeline`, 6 stages + observability) | ✅ |
| LLM client layer (`LLMClient` protocol, Ollama/Gemma, mock) | ✅ |
| SQL generation from NL (`SQLGenerator` + prompt) | ✅ |
| Result analysis (`ResultAnalyzer` + prompt) | ✅ |
| Question orchestrator (`QuestionToSQLPipeline`) | ✅ (question → SQL → execute) |
| Observability (logging + JSONL run records, per-stage timings) | ✅ |
| LangChain tools (`get_schema`, `run_sql`) | ✅ |
| **One class that does question → SQL → execute → analyze** | ⛔ not yet — analysis is wired manually in a test |
| **LangGraph agent loop / retry-on-rejection** | ⛔ not yet (LangGraph is a dep, unused) |
| **Real pytest suite** | ⛔ still manual `test_*.py` smoke scripts |

---

## 1. The big picture — how a question flows end to end

```
  "What were total net sales by month in 2025?"
        │
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│  QuestionToSQLPipeline.run(question)                                   │
│  ── stamps run_id, validates question is non-empty ──                  │
└───┬──────────────────────────────────────────────────────────────────┘
    │
    │ 1) SchemaProvider.get_schema_document()  ── live BigQuery metadata → text
    │
    │ 2) SQLGenerator.generate(question, schema)                          
    │        └─ build_sql_generation_prompt() → LLMClient.generate_response()
    │             └─ OllamaGemmaClient → Gemma (local) → raw text
    │        └─ strip ``` fences → candidate SQL
    │
    │ 3) SQLExecutionPipeline.execute(sql)   ◄── THE GUARDRAILS (6 stages)
    │        Stage 1  read-only validation      (sqlglot)
    │        Stage 2  table-access allowlist     (sqlglot AST walk)
    │        Stage 3  result-row limit           (inject / cap LIMIT)
    │        Stage 4  dry run                     (BigQuery cost estimate)
    │        Stage 5  cost check                  (vs MAX_QUERY_BYTES)
    │        Stage 6  execution                   (read-only, timeout + billing cap)
    │        └─ every exit routes through _finalize_result → log + JSONL + timings
    │
    ▼
  structured result dict  { status, generated_sql, sql_execution: {rows, ...}, ... }
        │
        │ 4) (separate step, currently wired in test_result_analyzer.py)
        ▼
  ResultAnalyzer.analyze(question, sql, rows)
        └─ build_result_analysis_prompt() → LLM → written business analysis
```

**Core principle:** the LLM never touches BigQuery directly. It only ever produces *text*. That text (SQL) is treated as untrusted and must survive all six deterministic guardrail stages before a single byte is scanned. Cost, row count, and runtime are all bounded before and during execution.

---

## 2. Tech stack

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.11 | pinned via `.python-version`; venv is 3.11 (system default is 3.14) |
| Env/deps | `uv` | `pyproject.toml` + `uv.lock`; `requirements.txt` mirrored. Run with `uv run python ...` |
| Warehouse | Google BigQuery | `google-cloud-bigquery` |
| Auth | Application Default Credentials | `gcloud auth application-default login`; no service-account key committed |
| SQL parse/validate | **`sqlglot`** | statement classification + AST table-access walk + LIMIT injection |
| LLM (SQL gen + analysis) | **Ollama + Gemma**, local | `ollama` python client; model from `OLLAMA_MODEL` |
| Tool interface | **`langchain`** | `@tool` `get_schema` / `run_sql` — the agent-facing surface |
| Config | `python-dotenv` | `.env` overrides, sensible defaults in `src/config.py` |
| Observability | stdlib `logging` + JSONL | `logs/agent_run.log` + `logs/runs.jsonl` |
| Planned | **LangGraph** | dependency present, not yet used (the reasoning/retry loop) |

---

## 3. Project structure

```
src/
├── config.py                 # ALL settings + env overrides (project, caps, ollama, logging)
├── bigquery_service.py       # BigQueryService — the only code that talks to BigQuery
├── schema_config.py          # RELATIONSHIPS — hand-written FK join hints
├── schema_formatter.py       # format_dataset_structure() — metadata → compact LLM text
├── schema_provider.py        # SchemaProvider — live schema → document (wraps the two above)
├── sql_validator.py          # GUARDRAILS: validate_read_only_sql, validate_table_access,
│                             #             enforce_result_limit  (+ result dataclasses)
├── sql_execution_pipeline.py # SQLExecutionPipeline — 6-stage safe execution + observability
├── exceptions.py             # QueryExecutionTimeoutError, LLMProviderError
├── observability.py          # logger setup + JSONL run recorder + utc_timestamp
├── sql_generator.py          # SQLGenerator — NL question → SQL (via LLM), strips code fences
├── result_analyzer.py        # ResultAnalyzer — rows → written business analysis (via LLM)
├── question_to_sql_pipeline.py # QuestionToSQLPipeline — orchestrates question→SQL→execute
├── tools.py                  # LangChain tools: get_schema(), run_sql()
├── llm/
│   ├── base.py               # LLMClient (Protocol) + LLMResponse (dataclass)
│   ├── ollama_client.py      # OllamaGemmaClient — real local Gemma via Ollama
│   └── mock.py               # MockLLMClient — deterministic, no model needed
└── prompts/
    ├── sql_generation.py     # build_sql_generation_prompt() — 14 hard rules
    └── result_analysis.py    # build_result_analysis_prompt() — structured-output rules

Datasets/                     # synthetic business_insights dataset + DATA_DICTIONARY.md
test_*.py                     # ~23 manual smoke scripts (NOT pytest) — one per layer
logs/                         # agent_run.log + runs.jsonl (gitignored)
```

Despite the `test_*.py` names, **none are pytest suites** — no `assert`, `pytest` not installed. They're manual scripts run with `uv run python test_x.py`, each printing output to eyeball. See §9.

---

## 4. Configuration (`src/config.py`)

Every value is env-overridable; defaults match the real project. The safety-relevant ones:

| Setting | Default | Role |
|---|---|---|
| `PROJECT_ID` | `sql-bigquery-502206` | GCP project (also the table allowlist's project) |
| `DATASET_ID` | `business_insights` | dataset (also the allowlist's dataset) |
| `BIGQUERY_LOCATION` | `australia-southeast1` | region for all jobs |
| `MAX_QUERY_BYTES` | `100_000_000` (100 MB) | **cost cap** — dry-run reject + `maximum_bytes_billed` |
| `MAX_RESULT_ROWS` | `100` | **row cap** — LIMIT injected/reduced + client-side `max_results` |
| `QUERY_TIMEOUT_SECONDS` | `30` | **time cap** — job aborted + cancelled if exceeded |
| `MAX_ANALYSIS_ROWS` | `50` | rows sent to the analysis LLM (truncated) |
| `OLLAMA_HOST` | `http://localhost:11434` | local Ollama server |
| `OLLAMA_MODEL` | `gemma4:latest` | model tag (⚠️ verify this tag exists in `ollama list`) |
| `OLLAMA_TIMEOUT_SECONDS` | `120` | LLM call timeout |
| `OLLAMA_TEMPERATURE` | `0.1` | low = deterministic SQL |
| `LOG_DIRECTORY` | `logs` | |
| `APPLICATION_LOG_FILE` | `logs/agent_run.log` | human-readable log |
| `RUN_LOG_FILE` | `logs/runs.jsonl` | structured run records |

> ⚠️ `OLLAMA_MODEL=gemma4:latest` — double-check the tag; Gemma releases are `gemma`, `gemma2`, `gemma3`. If Ollama can't find it you'll get an `LLMProviderError` from the client.

---

## 5. The guardrails (the heart of the project)

All SQL — whether LLM-generated or hand-passed to the `run_sql` tool — goes through **`SQLExecutionPipeline.execute()`**, six stages, each able to reject/error early with a structured dict. Nothing runs until stages 1–5 pass.

| # | Stage | Function | Rejects when |
|---|---|---|---|
| 1 | Read-only validation | `validate_read_only_sql` | empty / bad syntax / >1 statement / not a `SELECT`/CTE (blocks DELETE, CREATE, INSERT, UPDATE, DROP, MERGE…) |
| 2 | Table-access allowlist | `validate_table_access` | any physical table not `project.dataset.table` fully-qualified, or not the configured project/dataset, or not a known table. CTE names are recognised and skipped. |
| 3 | Result-row limit | `enforce_result_limit` | injects `LIMIT MAX_RESULT_ROWS` if absent; reduces an existing LIMIT that's too high; **rejects** a non-integer/parameterised LIMIT |
| 4 | Dry run | `BigQueryService.dry_run_query` | BigQuery itself rejects it (unknown column, type error) — catches what sqlglot can't. Also produces the byte estimate. **No execution, no billing.** |
| 5 | Cost check | (in pipeline) | `estimated_bytes > MAX_QUERY_BYTES` |
| 6 | Execution | `BigQueryService.run_query` | runs read-only with `maximum_bytes_billed` + `job_timeout_ms`; a timeout raises `QueryExecutionTimeoutError`, the job is cancelled |

The full guardrail checklist:
1. **Read-only only** — single `SELECT`/CTE survives; all DML/DDL rejected.
2. **No statement stacking** — exactly one statement (`"...; DROP ..."` blocked).
3. **Syntax safety** — parse errors become clean rejections, not crashes.
4. **Table allowlist** — only the configured project + dataset + known tables; external projects & unqualified names blocked at the AST.
5. **CTE-aware** — WITH names not mistaken for physical tables.
6. **Bounded rows** — LIMIT is always present and ≤ `MAX_RESULT_ROWS`; the client also caps `max_results`.
7. **Cost pre-flight** — dry-run estimate over the cap ⇒ reject before running.
8. **Hard billing cap** — executed query carries `maximum_bytes_billed`; BigQuery aborts rather than over-bill.
9. **Time cap** — `job_timeout_ms`; on timeout the job is cancelled and a typed error returned.
10. **Structured, never-throws responses** — every path returns `{status, stage, error_type, message, ...}`; the agent gets machine-readable feedback, not exceptions.
11. **Observed** — every run is logged (info/warn/error by status) and appended to `runs.jsonl` with `run_id`, `duration_ms`, and per-stage timings.

Detailed stage-by-stage semantics (what each returns, edge cases) live in **[PIPELINE.md](PIPELINE.md)**.

---

## 6. The LLM layer

- **`LLMClient` (Protocol, `llm/base.py`)** — the only contract the rest of the code depends on: `generate_response(prompt: str) -> LLMResponse`. Anything satisfying it is swappable (real model, mock, future hosted model). `LLMResponse` carries `text`, `model_name`, token counts, and `response_time_ms`.
- **`OllamaGemmaClient` (`llm/ollama_client.py`)** — real implementation. Validates model/timeout/temperature, calls a local Ollama server, and maps `ResponseError`/`HTTPError` to a single `LLMProviderError` (so callers catch one type). Returns timing + token usage.
- **`MockLLMClient` (`llm/mock.py`)** — deterministic placeholder, no server needed — lets the pipeline be exercised without a model.
- **`SQLGenerator`** — builds the schema-grounded prompt, calls the LLM, and strips ```` ```sql ```` fences from the output. Returns `SQLGenerationResult(question, sql, raw_model_output, llm_response)`.
- **`ResultAnalyzer`** — truncates rows to `MAX_ANALYSIS_ROWS`, builds the analysis prompt, calls the LLM, returns `ResultAnalysisResult` (analysis text + how many rows were analysed + truncation flag + llm metadata).
- **Prompts** are deliberately strict (`prompts/`): the SQL prompt has **14 rules** (fully-qualified names, SELECT-only, no `SELECT *`, `SAFE_DIVIDE`, partition-pruning on `sale_date`, "revenue"→`net_revenue`, "profit"→`profit_amount`, no markdown…). The analysis prompt forces a fixed structure (DIRECT ANSWER / KEY INSIGHTS / SUPPORTING NUMBERS / LIMITATIONS / SUGGESTED FOLLOW-UP) and forbids inventing numbers or claiming causation.

The prompts are **belt-and-suspenders** with the guardrails: the prompt *asks* for read-only fully-qualified SQL; the guardrails *enforce* it regardless of what the model returns.

---

## 7. Observability (`src/observability.py`)

- `configure_application_logger()` — one logger (`word_to_insights`), console + file (`logs/agent_run.log`), idempotent (won't double-add handlers).
- `write_jsonl_record()` — appends one JSON object per line to `logs/runs.jsonl`, each stamped with `timestamp_utc`.
- Both pipelines call these on every outcome. `SQLExecutionPipeline` records `run_id`, `status`, `stage`, `duration_ms`, `stage_timings_ms` (per-stage perf_counter deltas), byte counts, row counts, cache hit, and the validated/executed SQL. `QuestionToSQLPipeline` adds its own `question_run_id` + `question_pipeline_duration_ms`.

---

## 8. The data model — `business_insights` dataset

Small (1,500 rows) but realistic star schema. Full spec in `Datasets/DATA_DICTIONARY.md`.

| Table | Grain | Rows | PK | FKs |
|---|---|---|---|---|
| `dim_customers` | 1/customer | 200 | `customer_id` | — |
| `dim_products` | 1/product | 40 | `product_id` | — |
| `fact_sales` | 1/transaction line | 1,260 | `sale_id` | `customer_id`, `product_id` |

```
dim_customers (1) ───< fact_sales (M) >─── dim_products (1)
```

**`fact_sales` financial formulas** (the business logic the agent must reason about):
```
gross_revenue   = unit_price × quantity
discount_amount = gross_revenue × discount_pct
net_revenue     = gross_revenue - discount_amount
tax_amount      = net_revenue × branch tax rate
total_price     = net_revenue + tax_amount
cost_amount     = product unit_cost × quantity
profit_amount   = net_revenue - cost_amount
```

**Embedded patterns** (real signal for analysis): Nov/Dec seasonality (Electronics extra holiday uplift), summer beverages/fruit, Aug/Sep stationery, `Classic Notebook` declining and `Premium Shampoo` growing through 2025, per-branch category preferences, members buy more / earn double reward points in December.

> `dataset_reference.md` documents an *alternate* public dataset (`thelook_ecommerce`) that was evaluated but is **not** loaded. Exploratory notes only.

---

## 9. Setup & running

```bash
# one-time
uv sync
gcloud auth application-default login
echo 'GCP_PROJECT_ID=sql-bigquery-502206' > .env
ollama pull gemma3           # or whatever tag OLLAMA_MODEL points at; ollama must be running

# end-to-end (needs BigQuery creds AND a running Ollama)
uv run python test_question_to_sql_pipeline.py   # question → SQL → execute
uv run python test_result_analyzer.py            # question → SQL → execute → analyze

# guardrails & pipeline (need BigQuery creds; no LLM)
uv run python test_sql_validator.py              # stage 1 cases
uv run python test_table_access_guardrail.py     # stage 2 cases
uv run python test_result_limit_guardrail.py     # stage 3 cases
uv run python test_dry_run_guardrail.py          # stage 4 (unknown column)
uv run python test_query_timeout.py              # stage 6 timeout path
uv run python test_sql_execution_pipeline.py     # all 6 stages
uv run python test_pipeline_observability.py     # logging + JSONL
uv run python test_stage_timings.py              # per-stage timings

# LLM layer (need Ollama; or use the mock)
uv run python test_llm_interface.py
uv run python test_ollama_gemma_client.py
uv run python test_sql_generator.py

# data layer (need BigQuery creds only)
uv run python test_bigquery_service.py
uv run python test_schema.py / test_table_metadata.py / test_dataset_structure.py
uv run python test_schema_formatter.py
uv run python test_run_query.py
```

Always use the project venv (`uv run` or `.venv/bin/python`) — deps don't exist in the system 3.14.

---

## 10. Bugs found & fixed during development (worth remembering)

Original data-layer bugs: `.env` never loaded (`load_dotenv` missing, "worked by accident" via gcloud default project); missing f-string braces; single-quotes instead of backticks on a table ref; wrong VS Code interpreter (3.14 vs venv 3.11); Pyrefly "src-layout" false positive (fixed with `[tool.pyrefly] search-path=["."]`); an `IndentationError` mid-edit.

Pipeline-era bugs (all fixed): `QueryExecutionTimeoutError` given `str | None` job_id → `... or "unknown"`; two mis-indented `except` blocks in `run_sql`; `int(dict[str,object])` type error → `cast(int, ...)`; `_finalize_result` written at class-level indent; every `execute()` return re-routed through `_finalize_result`; a **circular import** (`schema_provider` ↔ `tools`) fixed by deleting an unused `from src.tools import bigquery_service`; a duration bug (`round(secs,2)*1000` lost sub-second precision) → `round(secs*1000,2)`; and `ResultAnalyzer.__init` (missing `__`) so the class ignored its constructor.

Known leftover: `src/exceptions.py:1` has an unused `from google.cloud.bigquery._job_helpers import job_config_with_defaults` — safe to delete.

---

## 11. What's left / next steps

- **Fold analysis into the orchestrator** — `QuestionToSQLPipeline` currently stops after execution; result analysis is wired manually in `test_result_analyzer.py`. Make one class do question → SQL → execute → analyze.
- **LangGraph loop** — add reasoning/retry: when the pipeline returns a rejection or dry-run error, feed the message back to the LLM and let it fix the SQL (a real agent loop). LangGraph is already a dependency.
- **Real pytest suite** — convert the `test_*.py` scripts, mock BigQuery/LLM for the pure-logic bits so they run without creds or a model.
- **Verify `OLLAMA_MODEL`** and clean up the unused import in `exceptions.py`.
