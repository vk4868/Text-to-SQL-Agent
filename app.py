"""A web front end for the agent.

    uv run streamlit run app.py

Sits on top of InsightsAgent exactly as the CLI does — no logic lives here,
only presentation. Everything shown comes from the run record, so what you
see in the browser is what landed in logs/runs.jsonl.

Streamlit re-runs this script on every interaction, so the agent is invoked
only on an explicit ask and the result is cached in session state. Otherwise
expanding a section would re-run the whole pipeline, costing another
twenty-odd seconds and another BigQuery job.
"""

import pandas as pd
import streamlit as st

EXAMPLE_QUESTIONS = [
    "What were total net sales by month in 2025?",
    "Which product category generated the most profit in 2025?",
    "What were the top 5 products by net revenue in 2025?",
    "How did net revenue compare between members and non-members in 2025?",
    "What were net sales by month in 1999?",
]

QUESTION_KEY = "question"
RESULT_KEY = "result"
RUN_REQUESTED_KEY = "run_requested"


@st.cache_resource(show_spinner=False)
def get_agent():
    """Build the agent once per session; it caches the schema internally."""

    from src.agent import InsightsAgent

    return InsightsAgent()


def initialise_state() -> None:
    st.session_state.setdefault(QUESTION_KEY, "")
    st.session_state.setdefault(RESULT_KEY, None)
    st.session_state.setdefault(RUN_REQUESTED_KEY, False)


def request_run(question: str) -> None:
    """Queue a question to be answered on the next script run."""

    st.session_state[QUESTION_KEY] = question
    st.session_state[RUN_REQUESTED_KEY] = True


# ----------------------------------------------------------------------
# rendering — all of it reads from the run record, nothing recomputes
# ----------------------------------------------------------------------


def render_outcome(record: dict) -> None:
    outcome = record.get("outcome", {})
    status = outcome.get("status")

    if status == "success":
        st.success("Answered")
    elif status == "rejected":
        st.warning(
            f"Refused by a guardrail at stage "
            f"`{outcome.get('terminal_stage')}`"
        )
        st.caption(
            "The query was understood and declined on policy. This is the "
            "safety layer working, not a fault."
        )
    else:
        st.error(f"Failed at stage `{outcome.get('terminal_stage')}`")

    if outcome.get("error_message"):
        st.code(str(outcome["error_message"]), language="text")


def render_metrics(record: dict) -> None:
    sql = record.get("sql", {})
    data = record.get("data", {})
    cost = record.get("cost", {})
    runtime = record.get("runtime", {})
    tokens = record.get("tokens", {}).get("totals", {})

    columns = st.columns(5)
    columns[0].metric("Rows", data.get("row_count") or 0)
    columns[1].metric("Repairs", sql.get("repair_attempts", 0))
    columns[2].metric("Tokens", f"{tokens.get('total_tokens') or 0:,}")
    columns[3].metric(
        "Bytes billed", f"{cost.get('total_bytes_billed') or 0:,}"
    )
    columns[4].metric(
        "Time", f"{(runtime.get('total_duration_ms') or 0) / 1000:.1f}s"
    )


def render_grounding(record: dict) -> None:
    """Surface the deterministic checks on the written analysis."""

    analysis = record.get("analysis", {})

    ungrounded = analysis.get("ungrounded_numbers") or []
    violations = analysis.get("contract_violations") or []

    if ungrounded:
        st.error(
            "**Not derivable from the result:** "
            + ", ".join(str(value) for value in ungrounded)
        )
        st.caption(
            "These figures appear in the analysis above but are not in the "
            "returned rows, nor a subtotal, difference, share or aggregate "
            "of them. A flag means the number could not be checked against "
            "the data — a prompt to verify, not proof of an error."
        )

    if violations:
        st.warning(
            "**Format:** " + "; ".join(str(item) for item in violations)
        )

    if (
        not ungrounded
        and not violations
        and analysis.get("business_analysis")
    ):
        st.caption(
            "✓ Every figure in this analysis is present in, or derivable "
            "from, the returned rows."
        )


def render_trace(record: dict) -> None:
    trace = record.get("runtime", {}).get("node_trace", [])

    if not trace:
        st.caption("No node trace recorded.")
        return

    frame = pd.DataFrame(
        [
            {
                "node": entry.get("node"),
                "seconds": round(
                    (entry.get("duration_ms") or 0) / 1000, 2
                ),
                "ok": entry.get("ok", True),
            }
            for entry in trace
        ]
    )

    st.dataframe(frame, hide_index=True, width="stretch")
    st.bar_chart(frame.set_index("node")["seconds"])


def render_repairs(record: dict) -> None:
    repairs = record.get("sql", {}).get("repair_history") or []

    if not repairs:
        return

    with st.expander(f"Repair attempts ({len(repairs)})"):
        st.caption(
            "The first query failed a guardrail, so the error was fed back "
            "to the model to rewrite."
        )
        for entry in repairs:
            st.markdown(
                f"**Attempt {entry.get('repair_attempt')}** — failed at "
                f"`{entry.get('failure_stage')}`"
            )
            st.caption(str(entry.get("error_message", "")))
            st.code(str(entry.get("repaired_sql", "")), language="sql")


def render_record(record: dict) -> None:
    """Render one complete run."""

    render_outcome(record)
    render_metrics(record)

    final_sql = record.get("sql", {}).get("final_sql")
    if final_sql:
        st.subheader("SQL")
        st.code(final_sql, language="sql")

    rows = record.get("rows") or []
    if rows:
        st.subheader("Result")
        st.dataframe(pd.DataFrame(rows), width="stretch")
    elif record.get("outcome", {}).get("status") == "success":
        st.subheader("Result")
        st.info("The query ran successfully but matched no rows.")

    analysis_text = record.get("analysis", {}).get("business_analysis")
    if analysis_text:
        st.subheader("Analysis")
        st.text(analysis_text)

    render_grounding(record)
    render_repairs(record)

    with st.expander("Trace and run record"):
        render_trace(record)
        st.json(record, expanded=False)


# ----------------------------------------------------------------------


def main() -> None:
    st.set_page_config(
        page_title="SQL BigQuery Agent",
        page_icon="🔍",
        layout="wide",
    )

    initialise_state()

    st.title("SQL BigQuery Agent")
    st.caption(
        "Ask a business question. The model writes the SQL but is never "
        "trusted with it — six deterministic stages prove the query safe "
        "before it runs, and every figure in the written answer is checked "
        "against the data."
    )

    with st.sidebar:
        st.subheader("Try one of these")
        for index, example in enumerate(EXAMPLE_QUESTIONS):
            if st.button(example, key=f"example_{index}", width="stretch"):
                request_run(example)
                st.rerun()

        st.divider()
        st.caption(
            "Every query is read-only, restricted to one allowlisted "
            "dataset, row-capped, and cost-checked with a BigQuery dry run "
            "before execution."
        )

    with st.form("ask_form"):
        question = st.text_input(
            "Question",
            value=st.session_state[QUESTION_KEY],
            placeholder="What were total net sales by month in 2025?",
        )
        submitted = st.form_submit_button("Ask", type="primary")

    if submitted:
        asked = (question or "").strip()
        if asked:
            request_run(asked)
        else:
            st.info("Enter a question to begin.")

    # The agent runs ONLY when a run was explicitly requested. Streamlit
    # re-runs this script on every interaction, and each run is a real
    # BigQuery job plus two model calls.
    if st.session_state[RUN_REQUESTED_KEY]:
        st.session_state[RUN_REQUESTED_KEY] = False

        asked = st.session_state[QUESTION_KEY]

        with st.spinner(
            "Grounding in the schema, writing SQL, checking it…"
        ):
            st.session_state[RESULT_KEY] = get_agent().run(asked)

    record = st.session_state[RESULT_KEY]

    if record is None:
        st.info(
            "Ask a question above, or pick one of the examples in the "
            "sidebar."
        )
        return

    render_record(record)


if __name__ == "__main__":
    main()
