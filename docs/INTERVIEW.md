# Interview notes

The questions this design invites, and honest answers to them. If you can answer these you understand the project; if you cannot, re-read [PIPELINE.md](../PIPELINE.md).

---

## On the core idea

### "Walk me through what happens when I ask a question."

Question → stamp a run id and validate it isn't empty → fetch the **live** BigQuery schema and put it in the prompt → the model writes SQL → six deterministic stages prove that SQL safe → execute read-only under caps → the model turns rows into prose → a second checker proves every figure in that prose is derivable from the rows → classify, log, record.

The whole design follows from one line: **the model is untrusted and only emits text, so deterministic code has to prove that text safe.**

### "Why not just prompt it well?"

The prompt *does* ask for a single fully-qualified read-only query — 14 rules. The model mostly complies. "Mostly" is not a security property.

Prompts and guardrails deliberately overlap: the prompt asks, the guardrails enforce. If the model returns `DROP TABLE`, the prompt has failed and stage 1 still refuses it. And I can prove the enforcement — 19 adversarial queries, all refused, as a regression suite.

### "What's the most interesting thing you built?"

The prose checker. Everyone guards the SQL. But the model also writes the *explanation*, and on this model that is where it actually fails — 93% execution accuracy versus 80% grounding.

It extracts every figure from the analysis and requires it to be present in the rows, a bounded derivation of them (a column aggregate, a contiguous subtotal, a difference, a share of the total), or a number the question contained. In a live run the model wrote "Q4 Total: $8,637.64" when the real figure was $7,637.64 — off by exactly a thousand, while printing the correct addends next to it. Deterministic checking caught it; nothing else would have.

---

## On the guardrails

### "How do you stop it deleting data?"

Stage 1 parses with sqlglot and requires exactly one statement that is an `exp.Query`. `DELETE`, `DROP`, `UPDATE`, `INSERT`, `MERGE`, `CREATE` all fail that check. Requiring *exactly one* statement is what blocks `SELECT 1; DROP TABLE …`.

### "How do you stop it reading data it shouldn't?"

Stage 2 walks the AST for every `exp.Table` and requires it to be fully qualified `project.dataset.table`, in the configured project *and* dataset, and present in the live table list. An unqualified `fact_sales` is refused too — it would resolve against whatever default the session carries.

The subtlety is CTEs: `WITH fact_sales AS (SELECT … FROM bigquery-public-data…)` shouldn't treat `fact_sales` as a physical table. CTE names are collected first and skipped, while the genuinely foreign table inside is still caught. That's an adversarial test case.

### "How do you bound cost?"

Three ways, deliberately overlapping. A dry run estimates bytes without billing and rejects over the cap. The executed query carries `maximum_bytes_billed`, so if the estimate was wrong BigQuery aborts rather than over-bill. And `LIMIT` is injected or reduced, with a client-side `max_results` behind it.

### "What if the model writes `LIMIT @rows`?"

Refused. A bound the pipeline can't reason about is not a bound.

Interesting related finding: `LIMIT 10 + 90` is *not* refused, because stage 1's normalisation constant-folds it to `LIMIT 100` before stage 3 sees it. The row bound still holds — a folded 550 gets reduced to the cap — but through a different mechanism than the rule I thought was doing it. I found that writing the adversarial suite, and pinned the real behaviour with tests instead of leaving the doc wrong.

---

## On the architecture

### "Why LangGraph and not a while loop?"

I wrote the loop first; it's in the git history. The graph won on three things:

1. **Explicit state.** Accumulators are `operator.add` reducers, so a node returns only what it produced instead of hand-rolling read-modify-write.
2. **Inspectable routing.** "What happens after execution fails?" is a routing function consulting a named policy, not an `if` buried in a loop body.
3. **A single exit.** Every terminal path routes to `finalize_run`, so a run is classified, logged and recorded exactly once — including runs that fail. A test reads the builder's AST to enforce that no path goes to `END` directly.

### "What's the trap with those reducers?"

A node must return **only its new entries**. Return the whole list and `operator.add` appends it to what's there, duplicating everything. It's silent — the run still succeeds, the history is just wrong. There's a test per accumulator, and I verified they catch it by deliberately introducing the bug.

### "What if a node throws something you didn't expect?"

Then it escapes the graph and `finalize_run` never runs — so the run leaves no record, which defeats the point. `InsightsAgent.run` catches at the outermost boundary, logs the traceback, writes a crash record naming the node it escaped from, and returns it. The caller gets a result, not a traceback.

That was found by an audit, not by me. Worth saying so.

### "Why is a rejection not an error?"

They're different outcomes. `rejected` means the system worked and refused the query; `error` means something broke. Collapsing them makes the safety layer look like a fault and hides real faults among refusals. The evaluation depends on telling them apart — `guardrail_pass` fails on a refusal but not on a timeout.

---

## On evaluation

### "How do you know it's any good?"

15 golden questions, each with hand-written reference SQL, scored deterministically. The main metric is execution accuracy: run both queries, compare **result sets by value**, ignoring column names and column order. The agent can phrase the query however it likes as long as the numbers are right. That's the Spider/BIRD metric.

73% end to end, 93% on the SQL alone, against a 9.6 GB local model.

### "Why no LLM judge?"

Three reasons. It isn't reproducible — the same run scores differently. Using the same weak model to grade itself measures very little. And it wouldn't have caught the failures that mattered: the arithmetic errors needed *arithmetic*, not judgement.

### "Your pass rate moved a lot. Explain."

47% → 40% → 73%, and the model never changed. Every move was my measuring instrument getting more honest.

The first run scored 47%. I inspected each flag instead of accepting the number, and half were my own bugs: a regex capturing a trailing comma so "December 31, 2025" reported the token `31,`; percentages compared against absolute values so a `discount_pct` of 0.125 wouldn't support "12.5%".

Fixing those dropped it to 40%, which exposed the real problem — a category error. The prompt *requires* a `SUGGESTED FOLLOW-UP` section, so the model writes "compare against 2024", and I was reporting the prompt doing its job as a hallucination. Scoping the check to claim-bearing sections took it to 73%, stable across runs.

The lesson: a metric you haven't tried to break is a metric you don't understand.

### "How do you know your tests aren't vacuous?"

Mutation testing. Independent audits broke the source deliberately — removed the client injection seam, dropped subtotal grounding, made the zero-row path call the LLM, made the CLI return 0 on failure — and checked the suite fails. Most rounds caught everything; where a mutation survived, that was a real coverage gap and I wrote the missing test. Several tests in the suite exist only because a deliberate break slipped through first.

A green suite that cannot fail proves nothing.

---

## On the limitations

### "What's wrong with your grounding checker?"

Several things, and I'd rather name them than have you find them.

It checks **values, not attributions**. It can prove 87.3% exists in the result; it can't prove the model attached it to the right row. "Online is over 80%" passes when Online is 28% but In-Store plus Online is 87%. Catching that needs claim parsing.

Its strength depends on **data precision**. Sampling against this dataset's real result shapes puts false acceptance near 0% on decimal currency and around 1% on the count magnitudes it produces. The real hole is the small-integer ceiling: bare whole numbers of 12 or less are skipped as structural, so a fabricated "we lost 7 accounts" passes. Units are always checked, so "7%" and "$7" are not exempt.

**Ratios and subset means aren't enumerated**, so a model computing "4.5 times greater" gets reported. Over-reporting is the deliberate bias, but it's still noise.

And a flag means "not mechanically derivable", **not "false"**. It's a prompt to check.

### "What breaks at scale?"

Schema retrieval, first and hardest. The whole schema goes into every prompt — right for 3 tables, hopeless for 10,000. It becomes a retrieval problem: index table cards, retrieve the relevant handful, put those in the prompt.

The guardrails become *more* important there, not less: with 10,000 tables an allowlist stops being something a human can eyeball, so it has to be derived from a permissions model.

Latency too. Analysis is ~71% of a ~22s run and nothing streams, so the user waits for the whole thing.

### "What would you do next?"

In order: stream the analysis so the answer appears as it's written; make schema retrieval selective; add claim-attribution parsing to the grounding checker; and run the evaluation against a larger model to separate pipeline quality from model quality — I expect grounding to jump and execution accuracy to move very little.

---

## Questions to ask them

- How do you currently stop a generated query from doing something expensive or destructive?
- Do you verify model *output* against ground truth, or only evaluate at the prompt level?
- When a model-written answer is wrong, how do you find out — and how long does it take?
