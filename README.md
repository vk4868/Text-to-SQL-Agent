# BigQuery SQL Agent

Ask a business question in plain English. Get a validated, cost-capped, read-only BigQuery query, the rows it returned, and a written analysis of what they mean.

```
"What were total net sales by month in 2025?"
        │
        ▼
  live schema  →  LLM writes SQL  →  6 safety stages  →  BigQuery  →  written analysis
                                     (nothing runs until all six pass)
```

---

## Overview

This project is a natural-language-to-SQL agent for Google BigQuery. A local LLM (Gemma, served by Ollama) writes the SQL; deterministic Python decides whether that SQL is ever allowed to run.

The interesting part is not the text-to-SQL — it is everything around it. The model is treated as untrusted. It only ever produces *text*. That text has to survive a six-stage guardrail pipeline — read-only check, table allowlist, row limit, dry run, cost check, capped execution — before a single byte is scanned. When BigQuery rejects a query, the error is fed back to the model, which gets a bounded number of attempts to fix it.

Every run leaves an audit trail: a human-readable log line and a structured JSONL record with per-stage timings.

---

## Business Problem

In most companies, the people who need data and the people who can query it are not the same people.

A category manager wants to know whether the notebook range is still declining. A finance lead wants margin by branch for the last quarter. Neither writes SQL, so the question goes into a queue. An analyst turns it into a query, runs it, pastes the numbers back. The round trip takes hours or days. By the time the answer arrives, the decision has often already been made.

The obvious fix — "let an LLM write the SQL" — creates three new problems that stop it being used on real data:

| Risk | What it looks like in practice |
|---|---|
| **Damage** | A model that can emit `DELETE`, `DROP`, or `MERGE` against a production warehouse. |
| **Cost** | BigQuery bills by bytes scanned. One unbounded `SELECT *` over a large partitioned table is a real, unpleasant invoice. |
| **Wrong answers stated confidently** | A hallucinated column name is caught by the warehouse. A hallucinated *interpretation* of correct numbers is not. |

This project exists to answer the follow-up question that gets asked in every interview about text-to-SQL: **"How do you stop it doing something stupid or expensive?"**

---

## Solution

Split the system into two halves and only trust one of them.

**The non-deterministic half** — the LLM — converts language to SQL, and rows to prose. It is guided by strict prompts, but never relied on for safety.

**The deterministic half** — parser-based guardrails plus a thin BigQuery service — decides what actually runs. It parses the SQL into an abstract syntax tree, refuses anything that is not a single read-only query, refuses tables outside one approved dataset, forces a row limit, prices the query with a free dry run, rejects it if it is too expensive, and finally executes it with a hard billing cap and a timeout.

The prompts and the guardrails deliberately overlap. The prompt *asks* for safe SQL; the guardrails *enforce* it regardless of what comes back. If the model ignores every rule it was given, the outcome is a structured rejection message — not a modified table and not a surprise bill.

That rejection message is also the repair signal: it is fed back into the model with the original question and the live schema, and the corrected query goes through the same six stages again.

---

## Architecture

```
                        ┌───────────────────────────────────────────────┐
   question (English)   │        QuestionToInsightsPipeline             │
          │             │  (question → SQL → rows → written analysis)   │
          ▼             └───────┬───────────────────────────────────────┘
   ┌──────────────────────────┐ │
   │  QuestionToSQLPipeline   │◄┘
   └──┬───────────────────────┘
      │
      │ 1. SchemaProvider ─────────────► BigQuery metadata → compact schema text
      │
      │ 2. SQLGenerator ──────────────► prompt → LLM (Gemma via Ollama) → candidate SQL
      │
      │ 3. SQLExecutionPipeline ──────► ┌─────────────────────────────────────┐
      │                                 │ 1  read-only validation   sqlglot   │
      │                                 │ 2  table allowlist        sqlglot   │
      │                                 │ 3  result-row limit       sqlglot   │
      │                                 │ 4  dry run                BigQuery  │
      │                                 │ 5  cost check             Python    │
      │                                 │ 6  execution (capped)     BigQuery  │
      │                                 └─────────────────────────────────────┘
      │
      │ 4. on a repairable failure: SQLRepairer ──► LLM ──► back to step 3
      │    (bounded by MAX_SQL_REPAIR_ATTEMPTS)
      ▼
   rows + metadata ──► ResultAnalyzer ──► LLM ──► written business analysis

   Throughout: run_id, per-stage timings, log line + JSONL record at every exit.
```

Three orchestration layers, each usable on its own:

| Layer | Class | Does |
|---|---|---|
| Execution | `SQLExecutionPipeline` | Takes SQL from anywhere. Runs the six stages. Never raises — always returns `{status, stage, ...}`. |
| Question | `QuestionToSQLPipeline` | Question → schema → SQL → execution, with the repair loop. |
| Insights | `QuestionToInsightsPipeline` | The above, plus a grounded written analysis of the rows. |

The LLM sits behind an `LLMClient` protocol with one method, so the model is swappable and every pipeline can be tested with a scripted stand-in.

For a line-by-line walkthrough of one request from question to answer, see **[PIPELINE.md](PIPELINE.md)**. This file is the map; that file is the guided tour.

---

## Tech Stack

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.11 | Pinned in `.python-version`. |
| Warehouse | Google BigQuery (`google-cloud-bigquery`) | Dry runs and `maximum_bytes_billed` make cost control a first-class feature. |
| Auth | Application Default Credentials | No service-account key is committed or required. |
| SQL parsing | **`sqlglot`** | The guardrails walk a real AST. String matching on "DROP" is not a security control. |
| LLM | **Ollama + Gemma**, running locally | No data leaves the machine, no per-token cost, reproducible for anyone cloning the repo. |
| Tool interface | **`langchain`** | `@tool`-decorated `get_schema` / `run_sql` — the agent-facing surface. |
| Config | `python-dotenv` | Every setting env-overridable; see `.env.example`. |
| Observability | stdlib `logging` + JSONL | Human log for reading, JSONL for querying. |
| Testing | `pytest` | 160 tests, no credentials, no network. |

`langgraph` is installed as a transitive dependency of `langchain`, but this project imports no LangGraph API. The orchestration is plain Python, so the control flow is explicit and testable.

---

## Key Features

**Six deterministic guardrail stages.** Read-only enforcement, a project/dataset/table allowlist, a mandatory row limit, a free dry run, a cost check, and a capped execution. Each stage returns a structured result and its own timing.

**AST-based validation, not pattern matching.** Because `sqlglot` parses the statement, the checks survive things a regex would miss: a foreign table hidden inside a scalar subquery, a `LIMIT` that is a parameter rather than a number, a CTE alias that only looks like a table name, `UNNEST` that is not a table at all.

**An automatic repair loop.** When a query is rejected by a guardrail or by BigQuery's dry run, the failure message, the original question, and the live schema go back to the model for a corrected query — up to `MAX_SQL_REPAIR_ATTEMPTS` times. Failures that rewriting SQL cannot fix (an execution timeout, a warehouse error) are deliberately *not* retried.

**Schema grounding from live metadata.** The model is handed the real tables, columns, types, partitioning, clustering, and join relationships, discovered at runtime rather than hard-coded. Hallucinated column names are the most common text-to-SQL failure; this removes most of the opportunity.

**Grounded analysis, not free-form commentary.** The analysis prompt forbids inventing numbers and claiming causation, requires the model to declare when it has only seen a truncated sample, and enforces a fixed output structure.

**Structured failure everywhere.** No pipeline raises at the caller. Every path — rejection, warehouse error, dead LLM, empty question — returns `{status, stage, error_type, message}`. That is what makes an automated repair loop possible at all.

**Observability by default.** Every run gets a `run_id`, a log line at a severity matching its outcome, a JSONL record, and per-stage millisecond timings.

---

## Workflow

1. **Question validation.** Empty input is rejected before anything is spent.
2. **Schema grounding.** `SchemaProvider` reads live BigQuery metadata and renders a compact schema document, with hand-written join relationships appended (BigQuery does not expose foreign keys).
3. **SQL generation.** The question and the schema go into a 14-rule prompt. Markdown fences are stripped from the response.
4. **Stage 1 — read-only.** The SQL must parse, be exactly one statement, and be a query. `DELETE`, `INSERT`, `UPDATE`, `DROP`, `CREATE TABLE AS SELECT`, `MERGE`, `TRUNCATE`, `GRANT`, `EXPORT DATA`, `CALL`, `BEGIN…END`, and stacked `SELECT 1; DROP TABLE …` are all rejected here.
5. **Stage 2 — table allowlist.** Every physical table in the AST must be fully qualified and must be an approved table in the approved dataset in the approved project. CTE aliases are recognised and skipped; `UNNEST` is not treated as a table.
6. **Stage 3 — row limit.** A missing `LIMIT` is injected, an oversized one is reduced, and one that is not a fixed integer is rejected outright.
7. **Stage 4 — dry run.** BigQuery validates the query and estimates its bytes without executing or billing. This is where a hallucinated column is caught.
8. **Stage 5 — cost check.** Over `MAX_QUERY_BYTES`, the query is rejected before it runs.
9. **Stage 6 — execution.** Read-only, with `maximum_bytes_billed` as a hard backstop, a job timeout, and a client-side row cap. On timeout the job is cancelled.
10. **Repair (if needed).** A rejection at stages 1, 2 or 4 goes back to the model as a repair prompt, then re-enters step 4.
11. **Analysis.** Rows are truncated to `MAX_ANALYSIS_ROWS`, and the model writes a structured business answer that must declare its own limitations.

---

## Installation

Requires Python 3.11+.

```bash
git clone https://github.com/<your-username>/bigquery-sql-agent.git
cd bigquery-sql-agent

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

The test suite runs immediately after this step — it needs no cloud account and no LLM:

```bash
pytest
```

---

## Setup

Only needed to run against real data.

**1. Configuration.** Copy the example file and edit what you need. Every setting has a working default, so `.env` is optional.

```bash
cp .env.example .env
```

| Setting | Default | Role |
|---|---|---|
| `GCP_PROJECT_ID` | `sql-bigquery-502206` | GCP project. Also the only project the allowlist permits. |
| `BIGQUERY_DATASET_ID` | `business_insights` | The only dataset that may be queried. |
| `BIGQUERY_LOCATION` | `australia-southeast1` | Region for every job. |
| `MAX_QUERY_BYTES` | `100000000` (100 MB) | **Cost cap** — dry-run rejection and `maximum_bytes_billed`. |
| `MAX_RESULT_ROWS` | `100` | **Row cap** — `LIMIT` injected/reduced, plus a client-side cap. |
| `QUERY_TIMEOUT_SECONDS` | `30` | **Time cap** — job cancelled on expiry. |
| `MAX_SQL_REPAIR_ATTEMPTS` | `2` | How many times the LLM may fix rejected SQL. `0` disables repair. |
| `MAX_ANALYSIS_ROWS` | `50` | Rows handed to the analysis model. |
| `OLLAMA_HOST` | `http://localhost:11434` | Local Ollama server. |
| `OLLAMA_MODEL` | `gemma3:4b` | Model tag; must already be pulled. |
| `OLLAMA_TIMEOUT_SECONDS` | `120` | LLM call timeout. |
| `OLLAMA_TEMPERATURE` | `0.1` | Low, so SQL generation is close to deterministic. |
| `LOG_DIRECTORY` | `logs` | Where logs are written. |
| `APPLICATION_LOG_FILE` | `logs/agent_run.log` | Human-readable log. |
| `RUN_LOG_FILE` | `logs/runs.jsonl` | One JSON record per run. |

Real environment variables take precedence over `.env`.

**2. BigQuery access.**

```bash
gcloud auth application-default login
```

Then load the dataset. `Datasets/` contains the complete synthetic `business_insights` star schema as CSVs (1,500 rows across `fact_sales`, `dim_customers`, `dim_products`), with the full spec in `Datasets/DATA_DICTIONARY.md`. Create a dataset of that name in your project and load the three CSVs into matching table names.

```bash
python scripts/manual/check_bigquery_connection.py    # lists the tables it can see
```

**3. Local LLM.**

```bash
ollama pull gemma3:4b       # or set OLLAMA_MODEL to any tag you have
ollama serve

python scripts/manual/check_ollama_connection.py
```

---

## Usage

**Question to SQL to rows:**

```python
from src.bigquery_service import BigQueryService
from src.llm.ollama_client import OllamaGemmaClient
from src.question_to_sql_pipeline import QuestionToSQLPipeline
from src.schema_config import RELATIONSHIPS
from src.schema_provider import SchemaProvider
from src.sql_execution_pipeline import SQLExecutionPipeline
from src.sql_generator import SQLGenerator
from src.sql_repairer import SQLRepairer

bigquery_service = BigQueryService()
llm = OllamaGemmaClient()

pipeline = QuestionToSQLPipeline(
    schema_provider=SchemaProvider(
        bigquery_service=bigquery_service,
        relationships=RELATIONSHIPS,
    ),
    sql_generator=SQLGenerator(llm=llm),
    sql_execution_pipeline=SQLExecutionPipeline(
        bigquery_service=bigquery_service,
    ),
    sql_repairer=SQLRepairer(llm=llm),
)

result = pipeline.run("What were total net sales by month in 2025?")

print(result["status"])        # success | rejected | error
print(result["final_sql"])
print(result["sql_execution"]["rows"])
```

**Add the written analysis** by wrapping that pipeline in `QuestionToInsightsPipeline` with a `ResultAnalyzer` — see `scripts/manual/run_question_to_insights.py`.

**Run SQL directly through the guardrails**, bypassing the LLM:

```python
from src.tools import run_sql

run_sql.invoke({"sql": "SELECT COUNT(*) FROM `p.d.fact_sales`"})
```

**Runnable scripts** (each needs live credentials, and the first three need Ollama):

```bash
python scripts/manual/run_question_to_sql.py        # question → SQL → rows
python scripts/manual/run_question_to_insights.py   # the full workflow
python scripts/manual/run_sql_repair_demo.py        # forces a broken query through the repair loop
python scripts/manual/check_bigquery_connection.py
python scripts/manual/check_ollama_connection.py
```

---

## Example Questions and Outputs

The dataset supports questions like:

- What were total net sales by month in 2025?
- Which product categories grew fastest in the second half of the year?
- Which branch has the highest average profit per transaction?
- Do members spend more per order than non-members?
- Which products are declining year on year?

### What the guardrails do to a query

Given this candidate SQL for *"What were total net sales by month in 2025?"*, the pipeline normalises it, confirms the table is allowlisted, and appends the row limit. The SQL below is the actual output of stages 1–3, not an illustration:

```sql
SELECT
  FORMAT_DATE('%Y-%m', sale_date) AS sales_month,
  SUM(net_revenue) AS total_net_sales
FROM `sql-bigquery-502206.business_insights.fact_sales`
WHERE
  sale_date >= CAST('2025-01-01' AS DATE)
  AND sale_date < CAST('2026-01-01' AS DATE)
GROUP BY
  sales_month
ORDER BY
  sales_month
LIMIT 100
```

The result carries the decision trail:

```python
{
  "status": "success",
  "stage": "execution",
  "referenced_tables": ["sql-bigquery-502206.business_insights.fact_sales"],
  "result_limit_message": "Added a maximum result limit of 100 rows",
  "result_row_limit": 100,
  "limit_was_modified": True,
  "stage_timings_ms": {"read_only_validation": ..., "table_access_validation": ..., ...},
  "rows": [...],
}
```

### What the guardrails do to a hostile query

These are the exact rejection messages the code produces:

| Input | Outcome |
|---|---|
| `DELETE FROM \`…fact_sales\` WHERE sale_date < '2025-01-01'` | rejected at `validation` — *"Only read-only query statements are allowed. Received: Delete."* |
| `SELECT 1; DROP TABLE \`…fact_sales\`` | rejected at `validation` — *"Must contain exactly one statement, but found 2"* |
| `SELECT * FROM \`bigquery-public-data.thelook_ecommerce.orders\`` | rejected at `table_access` — *"Project 'bigquery-public-data' is not allowed. Only project 'sql-bigquery-502206' may be queried."* |
| `SELECT … LIMIT @row_limit` | rejected at `result_limit` — *"LIMIT must be a fixed integer value…"* |
| A query estimated at 500 MB | rejected at `cost_check` — *"Query exceeds the configured processing limit."* |

None of these execute, and none of them bill anything. The first four are rejected before BigQuery is contacted at all; the cost-check rejection is based on a dry run, which BigQuery performs for free.

### What the analysis step produces

`ResultAnalyzer` requires the model to answer in a fixed structure and to declare its own limitations:

```
DIRECT ANSWER:
<a direct response to the business question>

KEY INSIGHTS:
- <two to four observations, each supported by the returned rows>

SUPPORTING NUMBERS:
- <the specific figures the answer rests on>

LIMITATIONS:
<e.g. "Analysis is based on the first 50 of 1,260 rows.">

SUGGESTED FOLLOW-UP:
<one useful next question>
```

The wording of any given answer depends on the model and is not deterministic; the structure and the grounding rules are enforced by the prompt, and truncation is always declared.

---

## Project Structure

```
├── src/
│   ├── config.py                       # every setting, .env loaded here
│   ├── bigquery_service.py             # the only code that talks to BigQuery
│   ├── schema_config.py                # RELATIONSHIPS — hand-written join hints
│   ├── schema_formatter.py             # metadata → compact LLM-readable text
│   ├── schema_provider.py              # live schema → schema document
│   ├── sql_validator.py                # GUARDRAILS: read-only, allowlist, row limit
│   ├── sql_execution_pipeline.py       # the six stages + observability
│   ├── question_to_sql_pipeline.py     # question → SQL → execute, with repair loop
│   ├── question_to_insights_pipeline.py# the above + written analysis
│   ├── sql_generator.py                # question → SQL via the LLM
│   ├── sql_repairer.py                 # failed SQL + error → corrected SQL
│   ├── sql_text.py                     # shared code-fence stripping
│   ├── result_analyzer.py              # rows → written business analysis
│   ├── observability.py                # logger + JSONL run recorder
│   ├── exceptions.py                   # QueryExecutionTimeoutError, LLMProviderError
│   ├── tools.py                        # LangChain tools (lazily constructed)
│   ├── llm/
│   │   ├── base.py                     # LLMClient protocol + LLMResponse
│   │   ├── ollama_client.py            # local Gemma via Ollama
│   │   └── mock.py                     # deterministic stand-in
│   └── prompts/
│       ├── sql_generation.py           # 14 hard rules
│       ├── sql_repair.py               # repair prompt
│       └── result_analysis.py          # grounded-analysis rules
├── tests/                              # 160 offline tests (no credentials, no network)
│   ├── conftest.py                     # stub warehouse + scripted LLM
│   ├── test_sql_validator_read_only.py
│   ├── test_sql_validator_table_access.py
│   ├── test_sql_validator_result_limit.py
│   ├── test_sql_execution_pipeline.py
│   ├── test_question_to_sql_pipeline.py
│   ├── test_question_to_insights_pipeline.py
│   ├── test_bigquery_service.py
│   ├── test_schema_formatter.py
│   ├── test_result_analyzer.py
│   ├── test_sql_text.py
│   ├── test_observability.py
│   └── test_config_and_tools.py
├── scripts/manual/                     # live demos — need credentials, not run by pytest
├── Datasets/                           # the synthetic dataset + DATA_DICTIONARY.md
├── PIPELINE.md                         # line-by-line walkthrough of one request
├── dataset_reference.md                # an alternative dataset that was evaluated, not used
├── .env.example                        # every setting, documented
├── requirements.txt                    # pinned
└── pyproject.toml                      # project + pytest + ruff config
```

---

## Testing

```bash
pytest
```

```
160 passed
```

The suite runs with **no Google Cloud credentials, no network access, and no LLM server**. A stub BigQuery service and a scripted LLM client stand in for both, so behaviour is asserted rather than eyeballed, and nothing in the suite can create a billable job.

| Area | Tests | What is asserted |
|---|---|---|
| Read-only guardrail | 23 | Every DML/DDL verb rejected — `DELETE`, `INSERT`, `UPDATE`, `DROP`, `CREATE TABLE AS SELECT`, `MERGE`, `TRUNCATE`, `GRANT`, `EXPORT DATA`, `CALL`, `ALTER`, `BEGIN…END`. Stacked statements rejected. Comment-hidden `DROP` stays inert. Valid `SELECT`, CTE and `UNION` accepted. |
| Table allowlist | 19 | Foreign project, foreign dataset, unknown table, unqualified name, `INFORMATION_SCHEMA`, wildcard `fact_sales_*` all rejected. A foreign table hidden in a scalar subquery or a CTE is still caught. CTE aliases skipped; `UNNEST` not treated as a table. |
| Result limit | 13 | Missing `LIMIT` injected, oversized reduced, compliant left alone. `LIMIT @param` and `LIMIT 10+10` rejected. A `LIMIT` that exists only inside a subquery still gets an outer limit. |
| Execution pipeline | 19 | Each of the six stages produces the right `{status, stage, error_type}`. Cost, row and time caps reach BigQuery. Per-stage timings recorded in order; only the stages that ran appear. One JSONL record per outcome. Never raises. |
| Question pipeline | 16 | Repair loop: rejection → repair → success; two repairs; budget exhausted; repair disabled; dry-run failures repairable; execution failures deliberately not. Dead LLM and schema failures reported as structured errors. |
| Insights pipeline | 7 | End-to-end success, analysis skipped when SQL fails, truncation declared, all three layers observable in one run. |
| BigQuery service | 12 | Metadata mapping, partition/clustering reporting, unknown-table errors — and that a three-table dataset costs one listing plus one fetch per table. |
| Schema formatter | 9 | Structure, optional lines, relationships, ordering. |
| Result analyzer | 8 | Row truncation, truncation declared to the model, empty results, grounding rules present in the prompt. |
| SQL cleaning | 15 | Code fences stripped for both the generator and the repairer. |
| Observability | 8 | JSONL appended and timestamped, log directory created, unserialisable values survive, handlers not duplicated. |
| Config and tools | 11 | `.env` values reach the config, real environment variables win, `.env.example` documents every setting, and importing any module — including `src.tools` — constructs no BigQuery client. |

`scripts/manual/` holds the live end-to-end demos. They are excluded from pytest (`testpaths = ["tests"]`) precisely because they create real BigQuery jobs.

---

## Challenges and Learnings

**Blocking bad SQL properly means parsing it.** The first instinct is to look for dangerous keywords in the string. That fails immediately: `-- ; DROP TABLE x` is harmless, a `DROP` inside a CTE name is not what it looks like, and a foreign table can hide inside a scalar subquery where no simple scan will find it. Parsing to an AST with `sqlglot` and asking structural questions — *is this exactly one statement, and is it a query?*, *what physical tables does this reference?* — turned a leaky check into a solid one.

**A bounded row count is subtler than "add LIMIT".** A `LIMIT` that is a query parameter, or an expression like `10+10`, cannot be reasoned about ahead of time, so it is rejected rather than trusted. A `LIMIT` that exists only inside a subquery does not bound what the outer query returns, so an outer limit is added anyway.

**Structured failure is what makes an agent loop possible.** Once every stage returns `{status, stage, error_type, message}` instead of raising, two things fall out for free: the caller never crashes, and the failure message is exactly the input the repair prompt needs. The repair loop was a small amount of code on top of that decision — and knowing which failures are *not* worth retrying (an execution timeout is not a SQL problem) mattered as much as the retry itself.

**Configuration that is never loaded is worse than no configuration.** `.env` support was documented and `python-dotenv` was a dependency, but `load_dotenv()` was never called inside `src/`. Everything appeared to work because the default happened to match the real project — which is exactly the kind of bug that surfaces on someone else's machine. There is now a test that writes a `.env`, imports the config in a clean interpreter, and asserts the value propagated.

**Import-time side effects make code untestable.** Building the BigQuery client at module import meant `import src.tools` demanded credentials, which meant the pure-logic modules could not be tested offline at all. Moving to `functools.lru_cache` accessors made the whole suite runnable with no cloud account.

**Print-and-eyeball scripts are not tests.** There were 26 files named `test_*.py` and zero assertions between them. They were useful for exploring, but they proved nothing and could not fail. Replacing them with a stub warehouse and a scripted LLM changed the guardrails from "I believe these work" to "here is the adversarial case, and here is the assertion".

**Cheap correctness beats clever prompting.** The dry run is the single highest-value stage: BigQuery validates the query for free and tells you what is wrong in language a model can act on. A hallucinated column name costs nothing and gets fixed automatically.

---

## Limitations

- **One dataset, by design.** The allowlist permits exactly one project and one dataset. Multi-dataset use would need the allowlist and the schema document extended.
- **Single-shot, no memory.** Each question is independent. There is no conversation history and no follow-up context.
- **Local model quality.** Gemma at 4B parameters handles the aggregations this dataset invites; genuinely complex analytical SQL (window functions over multiple CTEs) is where it starts to need the repair loop. The `LLMClient` protocol means a stronger model is a one-line swap.
- **Repair is bounded and blind to semantics.** Two attempts by default. The loop fixes queries that *fail*; it cannot detect a query that runs successfully and answers the wrong question.
- **The analysis is only as good as the rows.** Row truncation is declared to the model and in the output, but an analysis of the top 50 rows is still an analysis of the top 50 rows.
- **Small dataset.** 1,500 synthetic rows keep the project free to run and reproducible. The architecture — partition pruning hints, dry runs, byte caps — is built for tables far larger than that, but has not been exercised at that scale.
- **No live BigQuery test in CI.** The offline suite covers logic exhaustively; the warehouse integration is exercised by the manual scripts, deliberately, so the test suite can never bill anyone. That means an API change in `google-cloud-bigquery` itself would not be caught automatically.

---

## Future Improvements

- **A real agent loop.** The tool surface (`get_schema`, `run_sql`) already exists; a graph-based agent could decide *when* to look at the schema, re-query after seeing results, and ask a clarifying question when a request is ambiguous.
- **Semantic validation.** Check the generated SQL against the question's intent — e.g. confirm the requested date range actually appears in the `WHERE` clause — to catch queries that succeed while answering the wrong question.
- **Result caching.** Identical questions currently re-run identical queries; hashing the normalised SQL would make repeats free.
- **Analytics on the agent itself.** `runs.jsonl` is already shaped for it: load it into BigQuery and track rejection rates by stage, repair success rate, and p95 latency per stage.
- **A thin UI.** A small Streamlit front end would make this demonstrable to a non-technical audience without a terminal.
- **CI.** The suite needs no secrets, so GitHub Actions can run `ruff` and `pytest` on every push as-is.

---

## Skills Demonstrated

**Data engineering** — BigQuery schema discovery from live metadata, partitioning and clustering awareness, dry-run cost estimation, `maximum_bytes_billed` and job timeouts, a star-schema dataset designed with documented grain, keys and financial formulas.

**SQL** — AST-level analysis with `sqlglot`, dialect-aware normalisation, safe `LIMIT` injection, CTE and subquery handling, and the join semantics of a fact/dimension model.

**LLM engineering** — prompt design with explicit, testable rules; schema grounding to suppress hallucination; a protocol-based client abstraction that makes the model swappable and the system testable; an error-driven repair loop with an explicit budget and an explicit list of what is worth retrying.

**Software engineering** — dependency injection throughout, layered pipelines that are each independently usable, structured error handling that never raises at the boundary, lazy construction to keep imports side-effect free, and a 160-test suite that runs with no external services.

**Production thinking** — treating the model as untrusted input, defence in depth (the prompt asks, the guardrails enforce, BigQuery caps), bounded cost/rows/time on every path, structured audit logging with per-stage timings, and configuration that is documented, overridable and tested.

**Communication** — a README and a walkthrough written for two audiences at once, and documentation that states plainly what the project does *not* do.

---

*Built by Vineet Kumar. The dataset in `Datasets/` is synthetic and safe to publish.*
