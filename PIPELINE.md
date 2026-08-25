# One request, end to end

The [README](README.md) is the map. This is the tour: a single question travelling through every node, with the reasoning behind each step. Read it top to bottom and you can explain the system cold.

Worked example throughout:

> **"What were total net sales by month in 2025?"**

---

## The shape of it

```
QUESTION
   │
   ├─▶ [A] initialize_run    stamp identity, validate the question
   ├─▶ [B] get_schema        live BigQuery metadata → text
   ├─▶ [C] generate_sql      schema-grounded prompt → LLM → candidate SQL
   ├─▶ [D] execute_sql       ◄── SIX GUARDRAIL STAGES
   │        └─ on a repairable failure → repair_sql → back to [D]
   ├─▶ [E] analyze_result    rows → prose → deterministic checks
   └─▶ [F] finalize_run      classify, log, record

Throughout: node timings, token accounting, one run record.
```

`src/agent.py::InsightsAgent` owns the compiled graph. `src/cli.py` and the evaluation harness both go through it; nothing else should.

---

## [A] `initialize_run`

Stamps `graph_run_id` (uuid4) and `run_started_at`, resets the per-run fields, and **validates the question before spending anything**. An empty question routes straight to `finalize_run`, costing neither an LLM call nor a BigQuery job.

It deliberately does *not* initialise the accumulators. `node_trace`, `repair_history`, `llm_calls` and `sql_execution_run_ids` are `Annotated[list, operator.add]` reducers, so LangGraph merges whatever a node returns. Returning `[]` would be a no-op, and a missing key already reads as empty.

> **The one rule to remember about this graph:** a node returns **only its new entries**. Returning the whole list appends it to what is already there and duplicates all prior history. It is the easiest way to break this system, and there is a test for each accumulator that catches it.

---

## [B] `get_schema`

**The model must see the real schema, not guess it.** Hallucinated column names are the top text-to-SQL failure mode, so the live truth goes into the prompt.

`SchemaProvider.get_schema_document()` walks the dataset via `BigQueryService`, formats it compactly, and appends the hand-written `RELATIONSHIPS` from `schema_config.py` — **BigQuery does not expose foreign keys**, so the join structure has to be told.

```
DATASET: sql-bigquery-502206.business_insights

TABLE: fact_sales
ROW COUNT: 1260
PARTITIONED BY: sale_date (DAY)
CLUSTERED BY: customer_id, product_id
COLUMNS:
- sale_id STRING REQUIRED
- sale_date DATE REQUIRED
- net_revenue NUMERIC REQUIRED
  ...

RELATIONSHIPS:
- fact_sales.customer_id = dim_customers.customer_id
- fact_sales.product_id = dim_products.product_id
```

Building this costs roughly four API calls per table and it is requested again on **every repair attempt**, so it is cached behind `SCHEMA_CACHE_TTL_SECONDS` (default 300). `SchemaProvider.fetch_count` exists so tests can prove the cache works.

A `GoogleAPIError` here becomes `error_stage="schema_retrieval"` and routes to `finalize_run`.

---

## [C] `generate_sql`

`build_sql_generation_prompt` embeds the question and the live schema under **14 rules**. Each one exists for a reason, and several exist *because a guardrail will otherwise reject the query*:

| Rule | Why |
|---|---|
| Only schema tables and columns | anti-hallucination |
| Fully-qualified `project.dataset.table` | stage 2 rejects anything else |
| Exactly one read-only query | stage 1 rejects anything else |
| No `SELECT *` unless asked for transactions | keeps rows and bytes down |
| `SAFE_DIVIDE` when dividing | avoids divide-by-zero |
| Filter `sale_date` for date periods | partition pruning, so the query is cheap |
| "sales"/"revenue" → `net_revenue`, "profit" → `profit_amount` | the dataset's business semantics |
| No markdown, no commentary | parseable output |

The LLM sits behind one Protocol — `LLMClient.generate_response(prompt) -> LLMResponse`. `OllamaGemmaClient` maps every provider failure to a single `LLMProviderError`; `ScriptedLLMClient` returns queued canned responses and is what makes the whole test suite hermetic.

`SQLGenerator._clean_sql_output` strips a markdown fence if the model added one. The token counts are captured into `llm_calls`.

---

## [D] `execute_sql` — the guardrails

The deterministic core. Each stage is timed; every exit is a structured dict, never an exception.

### Stage 1 · read-only validation
`sqlglot.parse(read="bigquery")`. Rejects empty input, parse errors, **more than one statement** (blocking `SELECT 1; DROP …`), and any statement that is not an `exp.Query`. Returns a normalised, pretty-printed form used by every later stage.

*A side effect worth knowing:* normalisation constant-folds arithmetic, so `LIMIT 10 + 90` becomes `LIMIT 100` before stage 3 sees it. The row bound still holds — a folded 550 is reduced to the cap — but it holds via a different mechanism than the validator's "no calculated LIMIT" rule, which is unreachable through the pipeline. This is pinned by tests rather than assumed.

### Stage 2 · table allowlist
Walks `find_all(exp.Table)`. Collects CTE names first so a `WITH` alias is not mistaken for a physical table, then requires every real table to be fully qualified, in the configured project *and* dataset, and present in the live table list. Blocks `bigquery-public-data.…`, an unqualified `fact_sales`, and a table that simply does not exist.

### Stage 3 · result-row limit
Injects `LIMIT MAX_RESULT_ROWS` when absent, reduces one that is too high, and **refuses** a parameterised limit — a bound it cannot reason about is not a bound. Its output becomes the `executed_sql`.

### Stage 4 · dry run
A real BigQuery job with `dry_run=True`. Validates against the actual schema and estimates bytes **without executing or billing**. This is where a hallucinated column dies — sqlglot cannot know the column list, BigQuery does.

### Stage 5 · cost check
`estimated_bytes > MAX_QUERY_BYTES` → rejected, with the estimate and the cap in the message.

### Stage 6 · execution
Read-only, with `maximum_bytes_billed` (BigQuery aborts rather than over-bill if the estimate was wrong), `job_timeout_ms` (a timeout cancels the job and raises a typed error), and `max_results` as a second belt over stage 3.

### Threat → guardrail

| Threat | Stopped by | Stage |
|---|---|---|
| Data modification | read-only `exp.Query` check | 1 |
| Injection via stacked statements | single-statement check | 1 |
| Malformed SQL crashing the app | parse error → structured refusal | 1 |
| Reading outside the sandbox | project/dataset/table allowlist | 2 |
| Runaway row counts | `LIMIT` injection + client cap | 3, 6 |
| Hallucinated columns | BigQuery dry run | 4 |
| Expensive scans | estimate **and** hard billing cap | 5, 6 |
| Hung queries | job timeout + cancel | 6 |
| Opaque failures | `{status, stage, error_type}` everywhere | all |

---

## [D′] `repair_sql`

On failure, `route_after_execution` consults `src/repair_policy.py` — the single definition of what is worth repairing.

**Repairable:** `validation`, `table_access`, `dry_run`. The model can plausibly fix these by rewriting.
**Not repairable:** `cost_check`, `result_limit`, `execution`, `execution_timeout`. The query was understood and refused on policy, or the infrastructure failed — retrying burns money for the same outcome.

Bounded by `MAX_SQL_REPAIR_ATTEMPTS` (default 2). The repair prompt receives the failure stage, the error message and the failed SQL, and each attempt appends one entry to `repair_history`.

---

## [E] `analyze_result`

**Zero rows never reach the model.** An empty result is the single most reliable way to get an invented analysis out of a small model, so it is answered with deterministic text and no LLM call at all.

Otherwise: rows are truncated to `MAX_ANALYSIS_ROWS`, and the prompt forces a fixed structure — `DIRECT ANSWER` / `KEY INSIGHTS` / `SUPPORTING NUMBERS` / `LIMITATIONS` / `SUGGESTED FOLLOW-UP` — under rules forbidding invented numbers, causal claims, and markdown tables. When rows were truncated the prompt says so, so the model tells the reader it saw a subset.

Then two deterministic checks run on what came back:

**`validate_analysis_structure`** — are the five sections present, and is it free of markdown tables? Violations are *reported*, not raised; a local model drifts occasionally and a demo should not die on formatting.

**`find_ungrounded_numbers`** — is every figure derivable from the rows? See the [README](README.md#checking-the-prose) for the grounding rules. Two design decisions matter:

- The **generated SQL is not passed as context.** It is the same untrusted output being checked; grounding prose in it would let a model launder a fabrication through its own `WHERE` clause.
- Only the **claim-bearing sections** are checked. `SUGGESTED FOLLOW-UP` is *asked* to discuss data outside the result ("compare against 2024"), so checking it reports the prompt working as a hallucination.

---

## [F] `finalize_run`

**Every terminal path routes here.** Not to `END` — a test reads the builder's AST to enforce that. This is the graph-level counterpart of the execution pipeline's `_finalize_result`, and it means a run is classified, logged and recorded exactly once no matter where it stopped.

It must therefore be total over states where execution, SQL and analysis are all absent.

Classification requires **positive evidence**: success is claimed only when `execution_result["status"] == "success"`, never from the mere presence of a result. Inferring it once caused a failed run to report success with exit code 0.

And a guardrail refusal is `rejected`, not `error`.

One thing the graph cannot cover: an *unexpected* exception propagates out and `finalize_run` never runs. So `InsightsAgent.run` catches it at the outermost boundary, logs the traceback, writes a crash record naming the node it escaped from, and returns it — the guarantee "every run leaves exactly one record" holds even for the runs most worth having a record of.

---

## The trace

```
graph_run_id = c3e4505a…
[A] question ok
[B] schema fetched (cached; fetch_count stays 1 across repairs)
[C] LLM → SELECT FORMAT_DATE('%Y-%m', sale_date) AS sales_month, …
[D] 1 read-only?      ✓ single SELECT
    2 tables allowed? ✓ fact_sales in project.dataset
    3 row limit       + injected LIMIT 100
    4 dry run         ✓ 16,272 bytes estimated
    5 cost check      ✓ under 100 MB
    6 execute         ✓ 12 rows, cache_hit=True
[E] analyse           ✓ 5 sections, every figure derivable
[F] finalize          success/complete, 33.0s, 3,052 tokens
```

One `sql_execution` record and one `graph_run` record land in `logs/runs.jsonl`, linked by id: `run_id ⊂ graph_run_id`.

---

## Which file answers which question

| Question | File |
|---|---|
| How do you stop it running `DELETE`? | `sql_validator.validate_read_only_sql` |
| How do you stop it reading other datasets? | `sql_validator.validate_table_access` |
| How do you bound rows, cost and time? | `enforce_result_limit`, dry run + `MAX_QUERY_BYTES`, `job_timeout_ms` |
| How does the model know the schema? | `schema_provider` + `schema_formatter` + `schema_config` |
| How do you know the analysis isn't invented? | `analysis_guard` + `analysis_contract` |
| What is worth retrying? | `repair_policy` |
| How is the model swappable and testable? | `llm/base.py` Protocol + `llm/scripted.py` |
| What does a run leave behind? | `run_record.build_graph_run_record` |
| How is any of this measured? | `evaluation/` |
