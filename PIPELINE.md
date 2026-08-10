# End-to-End Pipeline Walkthrough

This is the guided tour of a single request travelling through the whole system, from an English business question to a written analysis. The [README](README.md) is the map; this is the tour.

Worked example used throughout:

> **"What were total net sales by month in 2025?"**

---

## The 10,000-foot view

```
QUESTION (English)
   │
   ├─▶ [A] Question validation        QuestionToSQLPipeline.run()
   │
   ├─▶ [B] Schema grounding           SchemaProvider.get_schema_document()
   │
   ├─▶ [C] SQL generation             SQLGenerator.generate()  ──▶ LLM (Gemma/Ollama)
   │
   ├─▶ [D] Safe execution             SQLExecutionPipeline.execute()   ← 6 GUARDRAIL STAGES
   │        ├ 1 read-only validation
   │        ├ 2 table-access allowlist
   │        ├ 3 result-row limit
   │        ├ 4 dry run (cost estimate)
   │        ├ 5 cost check
   │        └ 6 execution (read-only, capped, timed)
   │
   ├─▶ [R] Repair loop (only if [D] failed repairably)
   │        SQLRepairer.repair() ──▶ LLM ──▶ back to [D], up to MAX_SQL_REPAIR_ATTEMPTS
   │
   └─▶ [E] Result analysis            ResultAnalyzer.analyze()  ──▶ LLM (Gemma/Ollama)
            orchestrated by QuestionToInsightsPipeline

Throughout: observability — run_id, per-stage timings, human log + JSONL record.
```

Three orchestration layers, each usable on its own:
- **`SQLExecutionPipeline`** is **[D]** — it takes SQL from anywhere and runs the six stages.
- **`QuestionToSQLPipeline`** covers **[A]–[D]** plus **[R]** (question → SQL → executed rows, repairing on the way).
- **`QuestionToInsightsPipeline`** wraps that and adds **[E]** (the written analysis).

All three return structured dictionaries, and all three write a log line and a JSONL record on every outcome.

---

## The one idea that explains every design choice

> The LLM is untrusted. It only ever emits **text**. That text is treated as hostile until deterministic code proves it safe.

So the architecture is two halves:
1. **Non-deterministic half** (the LLM): turns language into SQL, and rows into prose. Guided by strict *prompts*.
2. **Deterministic half** (the guardrails + BigQuery service): parses, validates, bounds cost/rows/time, executes read-only, and returns structured results. Nothing here trusts the model.

The prompts and the guardrails overlap on purpose (**belt and suspenders**): the prompt *asks* for safe SQL; the guardrails *enforce* it no matter what comes back.

---

## [A] Question validation — `QuestionToSQLPipeline.run(question)`

File: `src/question_to_sql_pipeline.py`

Steps:
1. Generate a `run_id` (`uuid4`) and record `started_at` (`perf_counter`).
2. Log `"Question pipeline started | question_run_id=..."`.
3. `cleaned_question = question.strip()`.
4. **Guard:** if empty → return early via `_finalize_result` with
   `{status: "rejected", stage: "question_validation", error_type: "QuestionValidationError"}` — before any LLM call or BigQuery job.

`_finalize_result` wraps every return: it stamps `question_run_id` and `question_pipeline_duration_ms` onto the result, logs one line at a level matching the status, and appends a `question_to_sql` JSONL record. (This is the question-level twin of the execution pipeline's own finalizer.)

**Why:** fail fast on garbage input before spending an LLM call or a BigQuery job.

---

## [B] Schema grounding — `SchemaProvider.get_schema_document()`

Files: `src/schema_provider.py`, `src/schema_formatter.py`, `src/schema_config.py`, `src/bigquery_service.py`

The point: **the model must see the real schema, not guess it.** Hallucinated column names are the #1 text-to-SQL failure mode, so we hand the model the live truth.

Flow:
1. `SchemaProvider.get_schema_document()` calls `BigQueryService.get_dataset_structure()`.
2. `get_dataset_structure()` lists the dataset **once**, then fetches each table **once**, and builds both halves from that single object:
   - table metadata → type, description, row count, size bytes, **partition field/type** (via `time_partitioning.type_`), `require_partition_filter`, **clustering fields**.
   - column metadata → per-column `{name, type, mode, description}`.
   The public helpers `get_table_metadata(name)` / `get_table_schema(name)` remain available for single-table use; each validates the table exists and raises a clear `ValueError` otherwise. They accept an optional `known_table_names` set so a caller that has already listed the dataset does not pay for another round trip — this is what keeps the schema document at one listing plus one fetch per table, rather than one listing per helper call.
3. `format_dataset_structure()` renders it into compact, token-efficient text — optional lines (description/partition/cluster) only appear when present.
4. The hand-written `RELATIONSHIPS` (from `schema_config.py`) are appended as join hints — **because BigQuery doesn't expose foreign keys**, the model must be told how the star schema joins.

Resulting document (shape):
```
DATASET: sql-bigquery-502206.business_insights

TABLE: fact_sales
ROW COUNT: 1260
TABLE TYPE: TABLE
PARTITIONED BY: sale_date (DAY)        # only if partitioned
CLUSTERED BY: customer_id, product_id  # only if clustered
COLUMNS:
- sale_id STRING REQUIRED — ...
- sale_date DATE REQUIRED
- net_revenue NUMERIC REQUIRED
  ...

RELATIONSHIPS:
- fact_sales.customer_id = dim_customers.customer_id
- fact_sales.product_id = dim_products.product_id
```

**Same document powers the LangChain `get_schema` tool** — a future agent calls `get_schema` to ground itself before writing SQL.

---

## [C] SQL generation — `SQLGenerator.generate(question, schema_document)`

Files: `src/sql_generator.py`, `src/prompts/sql_generation.py`, `src/llm/*`

Steps:
1. `build_sql_generation_prompt(question, schema_document)` assembles the prompt — validates both are non-empty, then embeds the **14 rules** + the question + the live schema.
2. `self.llm.generate_response(prompt)` → an `LLMResponse` (text + model + tokens + timing).
3. `clean_sql_output(text)` (in `src/sql_text.py`, shared with the repairer) strips an optional Markdown code fence (```` ```sql ````, ```` ```bigquery ````, ```` ``` ````) from the first/last lines; raises `ValueError` on an empty response.
4. Returns `SQLGenerationResult(question, sql, raw_model_output, llm_response)`.

The 14 prompt rules (why each matters):

| Rule | Purpose |
|---|---|
| Only schema tables/columns | anti-hallucination |
| Fully-qualified `project.dataset.table` | required by guardrail stage 2 |
| Exactly one read-only query | required by guardrail stage 1 |
| SELECT / CTE only; never INSERT/UPDATE/DELETE/DROP/CREATE/ALTER/TRUNCATE/MERGE | required by stage 1 |
| Don't invent names/relationships | anti-hallucination |
| No `SELECT *` unless transaction-level asked | keeps bytes/rows down |
| `SAFE_DIVIDE` when division possible | avoids divide-by-zero errors |
| Filter `sale_date` for date periods | **partition pruning** = cheaper queries |
| "sales"/"revenue" → `net_revenue`; "profit" → `profit_amount` | encodes the dataset's business semantics |
| Clear aliases; no markdown/comments; SQL only | clean, parseable output |

### The LLM layer beneath

- **Contract** — `LLMClient` Protocol (`llm/base.py`): a single method `generate_response(prompt) -> LLMResponse`. Everything depends only on this, so the model is swappable.
- **Real** — `OllamaGemmaClient` (`llm/ollama_client.py`): validates `model_name`/`timeout`/`temperature` (0–2), calls a local Ollama server (`OLLAMA_HOST`, model from `OLLAMA_MODEL`, default `gemma3:4b`, temp `0.1` for determinism), times the call, and — crucially — **maps `ollama.ResponseError` and `httpx.HTTPError` to one `LLMProviderError`** so upstream code catches a single type. Returns token usage + `response_time_ms`.
- **Mock** — `MockLLMClient` (`llm/mock.py`): returns a fixed string; lets the pipeline run with no model.

`QuestionToSQLPipeline` wraps generation in `try/except (LLMProviderError, ValueError)` → on failure returns `{status: "error", stage: "sql_generation"}`. So a dead Ollama or an empty model response is a clean, structured failure, not a crash.

---

## [D] Safe execution — `SQLExecutionPipeline.execute(sql)`  ← THE GUARDRAILS

File: `src/sql_execution_pipeline.py` (validators in `src/sql_validator.py`)

This is the deterministic core. The candidate SQL now runs a 6-stage gauntlet. **Each stage records its own timing** (`stage_timings_ms[...] = round((perf_counter()-t)*1000, 2)`), and **every exit — reject, error, or success — is routed through `_finalize_result`**, which stamps `run_id` + `duration_ms` + `stage_timings_ms`, logs at the right level, and writes a JSONL record.

The pipeline is a **configurable class**: `SQLExecutionPipeline(bigquery_service, max_query_bytes, max_result_rows, query_timeout_seconds, logger)`. The constructor rejects non-positive caps.

### Stage 1 — Read-only validation · `validate_read_only_sql(sql)`
Returns an `SQLValidationResult(is_valid, message, normalized_sql, referenced_tables)`.
- Empty → reject.
- `sqlglot.parse(sql, read="bigquery")`; `ParseError` → reject (`"SQL syntax is invalid"`).
- **Exactly one** statement, else reject (blocks stacked `SELECT 1; DROP ...`).
- The statement must be an `exp.Query` (a SELECT or CTE). Anything else (DELETE, CREATE, INSERT, UPDATE…) → reject.
- On success returns a **normalized, pretty-printed** version of the SQL, which every later stage uses.

*Reject shape:* `{status: "rejected", stage: "validation", error_type: "SQLValidationError"}`.

### Stage 2 — Table-access allowlist · `validate_table_access(sql, allowed_project_id, allowed_dataset_id, allowed_table_names)`
Walks the AST (`statement.find_all(exp.Table)`) and, for **every** physical table:
- First collects CTE names (`find_all(exp.CTE)`); a bare name matching a CTE is **skipped** (it's not a physical table).
- Must be fully-qualified `project.dataset.table` — a bare `fact_sales` → reject.
- `project` must equal the allowed project (blocks `bigquery-public-data....`).
- `dataset` must equal the allowed dataset.
- `table` must be in `allowed_table_names` (from live `list_table_names()`) — blocks unknown/"secret" tables.
- Collects the approved fully-qualified paths into `referenced_tables`.

*Reject shape:* `{status: "rejected", stage: "table_access", error_type: "TableAccessValidationError"}`.

### Stage 3 — Result-row limit · `enforce_result_limit(sql, max_rows=MAX_RESULT_ROWS)`
Returns an `SQLLimitResult(is_valid, message, limited_sql, effective_limit, was_modified)`. Logic:
- No `LIMIT` present → **inject** `LIMIT max_rows` (`was_modified=True`).
- `LIMIT` present but **not a fixed integer literal** (a parameter or an expression) → **reject** (can't reason about the bound).
- `LIMIT n` with `n <= max_rows` → keep as-is (`was_modified=False`).
- `LIMIT n` with `n > max_rows` → **reduce** to `max_rows`.

The `limited_sql` it returns becomes the **`executed_sql`** used by all downstream stages. This guarantees the query can never return more than `MAX_RESULT_ROWS` rows regardless of what the model wrote.

*Reject shape:* `{status: "rejected", stage: "result_limit", error_type: "ResultLimitValidationError"}`.

### Stage 4 — Dry run · `BigQueryService.dry_run_query(executed_sql)`
- Runs a BigQuery job with `dry_run=True, use_query_cache=False`. **Validates the SQL for real and estimates bytes — without executing or billing.**
- Catches what sqlglot cannot: unknown columns, type mismatches, real BigQuery semantics.
- Returns `{estimated_bytes_processed, statement_type}`.
- A `GoogleAPIError` here → `{status: "error", stage: "dry_run"}` (this is where a hallucinated column name is caught).

### Stage 5 — Cost check (inline)
- `estimated_bytes = cast(int, dry_run_result["estimated_bytes_processed"])`.
- If `estimated_bytes > MAX_QUERY_BYTES` → `{status: "rejected", stage: "cost_check", error_type: "QueryCostLimitExceeded"}` with the estimate and the cap in the message.

### Stage 6 — Execution · `BigQueryService.run_query(executed_sql, maximum_bytes_billed, max_result_rows, query_timeout_seconds)`
- Runs read-only (`use_legacy_sql=False`).
- **`maximum_bytes_billed = MAX_QUERY_BYTES`** — BigQuery aborts rather than over-bill even if the estimate was wrong (defence in depth over stage 5).
- **`job_timeout_ms`** set; `result(timeout=...)`. On a `concurrent.futures.TimeoutError` the service **cancels the job** and raises `QueryExecutionTimeoutError(job_id, timeout_seconds, cancel_requested)`.
- **`max_results`** caps rows fetched client-side (second belt over stage 3's LIMIT).
- Returns rows + metadata: `rows`, `row_count`, `total_result_rows`, `result_truncated_by_client`, `job_id`, `statement_type`, `total_bytes_processed`, `total_bytes_billed`, `cache_hit`.

Two `except` arms:
- `QueryExecutionTimeoutError` → `{status: "error", stage: "execution_timeout", job_id, timeout_seconds, cancel_requested}`.
- `GoogleAPIError` → `{status: "error", stage: "execution"}`.

**Success shape:** `{status: "success", stage: "execution", validated_sql, executed_sql, referenced_tables, result_row_limit, limit_was_modified, estimated_bytes_processed, maximum_query_bytes, query_timeout_seconds, **rows/metadata}`.

### Guardrail summary table

| Threat | Guardrail | Stage |
|---|---|---|
| Data modification (DELETE/DROP/…) | read-only `exp.Query` check | 1 |
| SQL injection via stacked statements | single-statement check | 1 |
| Malformed SQL crashing the app | parse-error → structured reject | 1 |
| Querying tables outside the sandbox | project/dataset/table allowlist | 2 |
| Runaway row counts to the LLM/user | LIMIT injection + client `max_results` | 3, 6 |
| Hallucinated columns / type errors | BigQuery dry run | 4 |
| Expensive scans (cost) | dry-run estimate vs cap **+** `maximum_bytes_billed` | 5, 6 |
| Long-running / hung queries | `job_timeout_ms` + cancel | 6 |
| Opaque failures | structured `{status, stage, error_type}` everywhere | all |
| No audit trail | log + JSONL + per-stage timings | all |

---

## [R] The repair loop — `QuestionToSQLPipeline` + `SQLRepairer`

Files: `src/question_to_sql_pipeline.py`, `src/sql_repairer.py`, `src/prompts/sql_repair.py`

Stage [D] returns structured failures rather than raising, and that is precisely what makes an automatic retry possible: the rejection message is already in a form the model can act on.

The loop, inside `QuestionToSQLPipeline.run()`:

1. Execute the current SQL through the full six stages.
2. If `status == "success"` → done.
3. If the failure stage is **not** in `REPAIRABLE_STAGES = {validation, table_access, dry_run}` → stop. An execution timeout or a warehouse error is not something rewriting SQL will fix, so retrying would just burn time and money.
4. If the repair budget (`MAX_SQL_REPAIR_ATTEMPTS`, default 2) is spent → stop and return the last failure.
5. Otherwise call `SQLRepairer.repair(question, schema_document, failed_sql, error_message, failure_stage)`, which builds a repair prompt containing the original business question, the live schema, the SQL that failed, the exact error, and the stage it failed at — then cleans the model's answer with the same `clean_sql_output` the generator uses.
6. Append the attempt to `repair_history` (attempt number, failure stage, error message, failed SQL, repaired SQL, LLM metadata) and loop back to step 1 with the corrected SQL.

An `LLMProviderError` or `ValueError` during repair is itself returned as `{status: "error", stage: "sql_repair"}` — the loop never raises.

**Worked example.** The model writes `SUM(revenue)`, but the column is `net_revenue`:

```
attempt 1 → stage 4 dry run → BadRequest: "Unrecognized name: revenue"
          → repair prompt carries that message verbatim
          → model returns SUM(net_revenue)
attempt 2 → stages 1-6 pass → success, repair_attempts = 1
```

Because the error text is fed to the model verbatim, the *wording* of guardrail messages is functionally important, not cosmetic — a mangled message is a worse repair prompt.

The final result carries `generated_sql` (what the model first wrote), `final_sql` (what actually ran), `repair_attempts`, and the full `repair_history`.

---

## [E] Result analysis — `ResultAnalyzer.analyze(question, sql, rows, total_result_rows)`

Files: `src/question_to_insights_pipeline.py`, `src/result_analyzer.py`, `src/prompts/result_analysis.py`

`QuestionToInsightsPipeline` is the orchestrator for this stage. It calls `QuestionToSQLPipeline` first; if that did not succeed it returns immediately with the SQL-side status and stage, so no analysis is attempted on rows that do not exist. It then defensively checks that `sql_execution` is a dict and `rows` is a list (`stage: "result_validation"` if not) before handing them to `ResultAnalyzer`, and stamps `insights_run_id` + `insights_pipeline_duration_ms` on the way out, logging and writing a `question_to_insights` JSONL record.

`ResultAnalyzer` turns rows back into language. Steps:
1. Guard `total_result_rows >= 0`.
2. **Truncate** rows to `MAX_ANALYSIS_ROWS` (default 50) — never flood the model with the full result.
3. Compute `rows_were_truncated = total_result_rows > len(rows_for_analysis)`.
4. `build_result_analysis_prompt(...)` — serialises rows to JSON, embeds the question, the SQL, totals, and the truncation flag, under strict rules: **use only supplied numbers, don't invent, don't claim causation, state emptiness/truncation, no markdown tables.** Forces a fixed output structure: `DIRECT ANSWER / KEY INSIGHTS / SUPPORTING NUMBERS / LIMITATIONS / SUGGESTED FOLLOW-UP`.
5. `self.llm.generate_response(prompt)`; empty → `ValueError`.
6. Returns `ResultAnalysisResult(question, analysis, raw_model_output, rows_analyzed, total_result_rows, rows_were_truncated, llm_response)`.

**Why truncate + declare it:** honest analysis on a bounded sample beats a hallucinated summary of "all" the data. The `rows_were_truncated` flag is passed into the prompt so the model *tells the reader* when it's only seen a subset.

---

## The harness — `src/tools.py` (LangChain surface)

Two `@tool`-decorated functions are the agent-facing surface — what an autonomous agent loop would be handed. LangChain reads their signatures + docstrings to build the tool schema (the docstrings are written **for the model**):

- **`get_schema() -> str`** — returns the [B] schema document. Docstring tells the model to call it **before** writing SQL.
- **`run_sql(sql: str) -> dict`** — delegates to `SQLExecutionPipeline.execute()` — i.e. the entire [D] guardrail gauntlet. Docstring tells the model it only works after `get_schema` and that unsafe/expensive SQL is rejected.

The tools are backed by **lazily-constructed** singletons — `get_bigquery_service()`, `get_schema_provider()`, `get_sql_execution_pipeline()`, each a `functools.lru_cache` accessor. Nothing is built at import time, so importing `src.tools` never contacts Google Cloud; a missing credential surfaces when a tool is actually called. `reset_tool_dependencies()` clears the caches. Invoked as `run_sql.invoke({"sql": "..."})` / `get_schema.invoke({})`.

---

## Observability — what a run leaves behind

Files: `src/observability.py`, `src/config.py`

- **Human log** — `logs/agent_run.log` (and console). One logger `word_to_insights`, format `time | LEVEL | name | message`. Status drives level: success→`info`, rejected→`warning`, error→`error`.
- **Structured records** — `logs/runs.jsonl`, one JSON object per line, each stamped `timestamp_utc`. All three orchestrators write one, distinguished by an `event` field:
  - `event: "sql_execution"` — `run_id, status, stage, duration_ms, stage_timings_ms, job_id, error_type, message, referenced_tables, row_count, total_result_rows, estimated/total bytes, cache_hit, result_row_limit, limit_was_modified, validated_sql, executed_sql`.
  - `event: "question_to_sql"` — `question_run_id, status, stage, duration_ms, question, repair_attempts, error_type, message, generated_sql, final_sql`.
  - `event: "question_to_insights"` — `insights_run_id, status, stage, duration_ms, question, final_sql, repair_attempts, analysis_metadata`.
  One end-to-end question therefore leaves at least three correlated records, one per layer.
- **Per-stage timings** — `stage_timings_ms` is built stage-by-stage inside `execute()` (`read_only_validation`, `table_access_validation`, `result_limit_enforcement`, …), so you can see exactly where time went.

**Why JSONL:** append-only, one record per run, trivially greppable and loadable into BigQuery/pandas later for analytics on the agent itself.

---

## End-to-end trace of the example question

```
run_id = 7f3c...                                     [A] question ok, non-empty
schema_document = "DATASET: ...\nTABLE: fact_sales\n..."   [B] live schema fetched
prompt = "You are an expert BigQuery SQL analyst... {question} {schema}"   [C]
LLM →  SELECT FORMAT_DATE('%Y-%m', sale_date) AS month,
              SUM(net_revenue) AS total_net_sales
       FROM `sql-bigquery-502206.business_insights.fact_sales`
       WHERE sale_date BETWEEN '2025-01-01' AND '2025-12-31'
       GROUP BY month ORDER BY month
─ execute() ─                                        [D]
  1 read-only?         ✓ single SELECT
  2 tables allowed?    ✓ fact_sales in project.dataset
  3 row limit          + injected LIMIT 100 (was_modified=True)
  4 dry run            ✓ estimated_bytes = 24_010
  5 cost check         ✓ 24_010 ≤ 100_000_000
  6 execute            ✓ 12 rows, cache_hit=False, timed, billed ≤ cap
→ {status: success, sql_execution: {rows: [...12 months...]}}
(no repair needed here — had stage 1, 2 or 4 rejected it, [R] would have
 fed the message back to the model and re-entered [D])
─ analyze() ─                                         [E]
  rows ≤ 50, not truncated → prompt → LLM →
  "DIRECT ANSWER: Net sales in 2025 totalled ... peaking in December ..."
```

Every layer above wrote a log line and a JSONL record: `sql_execution` keyed by `run_id`, `question_to_sql` keyed by `question_run_id`, and `question_to_insights` keyed by `insights_run_id`.

---

## Cheat-sheet: which file does what

| Question you might get | Answer file(s) |
|---|---|
| "How do you stop it running DELETE?" | `sql_validator.validate_read_only_sql` (stage 1) |
| "How do you stop it querying other datasets?" | `sql_validator.validate_table_access` (stage 2) |
| "How do you bound rows / cost / time?" | `enforce_result_limit` (3), dry-run+`MAX_QUERY_BYTES` (4-5), `job_timeout_ms` (6) |
| "How does the model know the schema?" | `SchemaProvider` + `schema_formatter` + `schema_config` |
| "How is the model swappable / testable?" | `LLMClient` Protocol + `MockLLMClient` |
| "How do you handle a dead Ollama?" | `OllamaGemmaClient` → `LLMProviderError`, caught in the orchestrator |
| "How do you know what happened on a run?" | `observability.py` → `agent_run.log` + `runs.jsonl` + `stage_timings_ms` |
| "Where's the agent's tool interface?" | `tools.py` (`get_schema`, `run_sql`) |
| "How does it recover from bad SQL?" | `sql_repairer.py` + the repair loop in `question_to_sql_pipeline.py` |
| "How is any of this tested?" | `tests/` — 160 assertions, stub warehouse + scripted LLM, no credentials |
| "What's not built yet?" | A true agent loop that decides *when* to call each tool; semantic validation of intent; result caching; a UI |
