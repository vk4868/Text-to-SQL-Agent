# Notes: verified numerical answers

Branch `feature/verified-numerical-answers`, unsquashed commits above revision 1478190. Stopped at Phase 4.

## What I built

- Phase 1 (`a951487`, `3d96d16`, `1edf2f4`): evaluation tooling with a provenance manifest, frozen protocol (`evaluation/PROTOCOL.md`), expected facts, a keyed row-level dataset check, and the committed before report.
- Phase 2 (`8239488`, `3dd0b5e`): versioned metric registry (`src/semantics/`) with typed calculations, units, grain, ambiguous phrases and a decision log.
- Phase 3 (`c483d72`): strict query-intent parser, bounded SQL compiler with engine-owned bindings and result manifest (`src/semantic_query.py`); answer contract and parser (`src/analysis_contracts.py`).
- Phase 4 (`a19aaf5`, `edc77ca`): deterministic gate (`src/analysis_validation.py`): Decimal recomputation, entity, metric, unit and scope checks, coverage, stable reason codes.
- Not done: Phase 5 verifier, Phase 6 wiring into the graph, CLI and Streamlit, Phase 7 after report. The live application still publishes the old analysis with warnings.

## Decisions and trade-offs

- The existing guard checked whether a number was derivable somewhere in the results; this design also binds it to the correct metric, entity, units and scope. The model proposes; the engine owns ids, row refs, completeness and registry version.
- The standalone gate rejects the whole proposed answer on a detected mismatch. Ambiguous phrases return a clarification; nulls, negatives, undefined ratios and incomplete populations fail closed.
- Trade-off: blocking uncertain claims reduces incorrect publication but also rejects some legitimate questions, and the restriction adds complexity: a registry, compiler, contract and gate now stand where one prompt and one regex guard stood. The narrow compiler is a deliberate scope restriction: 14 of 15 golden shapes compile; group averages, shares, percentage change and multi-dimension grouping are rejected rather than guessed.
- "Correct" means verified against the supplied results and approved definitions; the gate does not establish that the source data or a business definition is itself correct.
- Anti-gaming rule, implemented and tested: an answerable numerical question must include relevant numerical evidence; empty prose or unrelated numbers cannot count as a pass.

## What I measured

Baseline of the unchanged app (live BigQuery, gemma4:latest, 3 trials x 15 golden + 19 attack cases): pooled 31/45 passed (9, 11, 11 of 15); execution accuracy 42/45; legacy grounding 33/45; guardrails 19/19; latency mean 19.2 s, p95 26.5 s. Report: `evaluation/reports/before_semantic_gate.md`; the historical `latest.*` is preserved. No after report exists; the new layer was exercised live only through read-only probes (four compiled queries matched expected facts; six correct claims passed, five injected faults blocked).

The live tables carried US geography while the CSVs are Australian; numeric checks missed it. With authorisation I reloaded the tables from the CSVs (originals backed up) and verified 0 differing cells before the baseline.

801 hermetic tests pass (432 at start) using `ScriptedLLMClient` and `FakeBigQueryService`, also with no credentials, a dead proxy and a dead Ollama host.

## What I'd do next

1. One bounded verifier call with a typed verdict; none after a deterministic reject.
2. Wire the layer into the graph with typed rendering and no draft leakage.
3. Rescore the baseline under the frozen rubric; run the after report on the same data, model and trials; add a price/average cohort.
4. Gaps: label-based subtotal guard, no group coverage above 12 rows, digits allowed in free-text contract fields.

## Where the agents helped or got in the way

Fable 5.1 orchestrated and verified every diff; Sonnet made prescribed edits; Opus built the registry, compiler and gate and ran separate reviews. Helped: reviews caught trust holes before commit (silent ambiguity settlement, a binder letting model SQL borrow compiler aliases, coverage laundered via evidence refs, a validator passing 14 mutations); a "wrong labels" finding exposed the dataset mismatch. Got in the way: implementers overstated test coverage and reinterpreted a rule unflagged; every report needed re-verification, and review loops cost feature time.


