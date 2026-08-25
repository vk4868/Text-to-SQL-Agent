# Portfolio notes

Every number here comes from `evaluation/reports/latest.json` or `logs/runs.jsonl`. Re-run `uv run python -m evaluation.run_evaluation` and update them rather than letting them drift.

---

## Resume bullets

Pick two or three. Each is tied to something measured, not asserted.

> **Built a text-to-SQL analytics agent over Google BigQuery** (Python, LangGraph, sqlglot, local Gemma via Ollama) that answers plain-English business questions with generated SQL, executes it read-only under enforced cost/row/time caps, and explains the result — **73% end-to-end pass rate on a 15-question golden set**, with **93% execution accuracy** on the SQL itself.

> **Designed a six-stage deterministic guardrail pipeline** that parses model-generated SQL to an AST and refuses anything unsafe before execution — **19/19 adversarial queries blocked** (DML, DDL, stacked statements, foreign datasets, unqualified tables, CTE shadowing), every one refused during parsing, before a byte was scanned.

> **Extended output verification from SQL into prose**: a deterministic checker proves every figure in the model's written analysis is present in, or derivable from, the query result. It caught real model arithmetic errors in live runs — including a quarterly total off by **$1,000** stated alongside its own correct addends.

> **Instrumented the agent end to end** with structured per-run records (token accounting, per-node timings, cost, guardrail outcomes), then used them to locate the real bottleneck: **analysis generation is 68% of a 21s run**, not the SQL work.

> **Built a reproducible evaluation harness with no LLM judge** — execution accuracy compares result sets by value against hand-written reference queries (Spider/BIRD-style), and a `--fail-under` threshold turns it into a CI gate rather than a number someone reads once.

> **Wrote 362 hermetic tests** that run with no cloud credentials and no model, using a scripted LLM fake and an injected BigQuery client; verified them by mutation testing rather than assuming a green suite means anything.

### If you need one line

> Built a schema-aware text-to-SQL agent on BigQuery where a local LLM writes the SQL but never gets trusted with it: six deterministic guardrail stages prove every query safe before execution, and a second checker proves every number in the written answer is actually derivable from the data.

---

## LinkedIn / summary paragraph

> I built a text-to-SQL business-insights agent on Google BigQuery, designed around one principle: the language model is untrusted and only ever produces text, so deterministic code has to prove that text safe before anything happens. A local Gemma model writes the SQL; a six-stage pipeline parses it to an AST, restricts it to an allowlisted dataset, bounds the rows, costs it with a BigQuery dry run and only then executes it read-only under a hard billing cap. All 19 adversarial queries in the regression suite are refused before a byte is scanned.
>
> The part I find most interesting is the second half. Most projects stop at guarding the SQL, but the model also writes the *explanation* — and that is where a small model actually fails. So every figure in the written analysis is checked against the numbers the query returned. It caught genuine arithmetic errors in live runs, including a quarterly total off by $1,000 printed right next to the correct addends. Measured on a 15-question golden set with no LLM judge: 93% execution accuracy on the SQL, 80% on the prose. That gap is the finding.

---

## Demo script — about 90 seconds

**Set up (10s).** "It answers business questions over a BigQuery dataset. The model writes the SQL, but it's never trusted with it."

**1 · The happy path (25s)**

```bash
uv run python main.py "Which product category generated the most profit in 2025?" --trace
```

Point at the trace line: schema → generate → execute → analyse, with per-node timings. Note that analysis dominates.

**2 · The guardrails (20s)**

```bash
uv run python -m evaluation.run_evaluation --guardrails-only
```

`19/19 adversarial queries refused` in about a second. "No LLM involved — these go straight into the pipeline. DROP, stacked statements, foreign datasets, a CTE shadowing a real table. All refused during parsing, before anything reaches BigQuery."

**3 · The interesting part (30s)**

Show a run where the guard fired:

```
⚠ NOT DERIVABLE FROM THE RESULT: $24,790.65
```

"The real total is $24,800.65. The model was out by ten dollars in its own summary. The SQL was perfect — the prose arithmetic wasn't. No prompt fixes that; only checking does."

**4 · The evidence (15s)**

```bash
uv run python scripts/report_runs.py
```

"Every run is recorded — tokens, per-node timing, cost, whether the analysis was grounded. That's how I know analysis is 68% of latency rather than guessing."

---

## Numbers worth remembering

| | |
|---|---|
| End-to-end pass rate | 11/15 (73%) |
| Execution accuracy | 14/15 (93%) |
| Analysis grounding | 12/15 (80%) |
| Adversarial queries refused | 19/19 (100%) |
| Mean latency / p95 | 21.0s / 25.2s |
| Mean tokens per question | 2,404 |
| Share of latency in analysis | ~68% |
| Tests | 362, hermetic |
| Dataset | 1,500 rows, 3 tables |

---

## What to say about the weak spots

Do not hide them — being able to name them precisely is the point.

**"73% seems low."** The failures are almost entirely in the *prose*, not the SQL: 93% execution accuracy against 80% grounding. And the model is a 9.6 GB local Gemma. A larger model would likely lift the grounding number with zero code changes — which is exactly the argument for having the checking layer.

**"Your checker has false positives."** It did, and finding them was most of the work. The first evaluation run scored 47%, and inspection showed half the failures were my own bugs — a regex capturing a trailing comma, percentages compared against absolute values, and a category error where I was checking the `SUGGESTED FOLLOW-UP` section that the prompt *requires* to discuss data outside the result. The number moved 47% → 40% → 73% with no change to the model. Every move was the instrument getting more honest.

**"How do you know the checker works?"** Mutation testing. Independent audits deliberately broke the source — deleted the injection seam, dropped the subtotal grounding, made the zero-row path call the LLM — and confirmed the suite fails. Where it did not fail, that was a real coverage gap and I closed it.

**"What would you do differently at scale?"** Schema retrieval. Dumping the whole schema into every prompt is correct for 3 tables and hopeless for 10,000 — it becomes a retrieval problem over table cards, and the guardrails become more important, not less, because the allowlist stops being something a human can eyeball.

---

## Related documents

- [README](../README.md) — architecture, guardrails, evaluation, limitations
- [PIPELINE](../PIPELINE.md) — one request end to end
- [INTERVIEW](INTERVIEW.md) — the questions this design invites
