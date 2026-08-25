"""A small web front end for the agent.

    uv run streamlit run app.py

Sits on top of InsightsAgent exactly as the CLI does — no logic lives here,
only presentation. Everything shown comes from the run record, so what you
see in the browser is what landed in logs/runs.jsonl.
"""

import pandas as pd
import streamlit as st

from src.agent import InsightsAgent

EXAMPLE_QUESTIONS = [
    "What were total net sales by month in 2025?",
    "Which product category generated the most profit in 2025?",
    "What were the top 5 products by net revenue in 2025?",
    "How did net revenue compare between members and non-members in 2025?",
    "What were net sales by month in 1999?",
]


@st.cache_resource(show_spinner=False)
def get_agent() -> InsightsAgent:
    """Build the agent once per session; it caches the schema internally."""

    return InsightsAgent()


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
        st.error(
            f"Failed at stage `{outcome.get('terminal_stage')}`"
        )

    if outcome.get("error_message"):
        st.code(outcome["error_message"], language="text")


def render_metrics(record: dict) -> None:
    sql = record.get("sql", {})
    data = record.get("data", {})
    cost = record.get("cost", {})
    runtime = record.get("runtime", {})
    tokens = record.get("tokens", {}).get("totals", {})

    columns = st.columns(5)
    columns[0].metric("Rows", data.get("row_count") or 0)
    columns[1].metric("Repairs", sql.get("repair_attempts", 0))
    columns[2].metric(
        "Tokens", f"{tokens.get('total_tokens') or 0:,}"
    )
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
            "the data — it is a prompt to verify, not proof of an error."
        )

    if violations:
        st.warning("**Format:** " + "; ".join(violations))

    if not ungrounded and not violations and analysis.get(
        "business_analysis"
    ):
        st.caption(
            "✓ Every figure in this analysis is present in, or derivable "
            "from, the returned rows."
        )


def render_trace(record: dict) -> None:
    trace = record.get("runtime", {}).get("node_trace", [])

    if not trace:
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


def main() -> None:
    st.set_page_config(
        page_title="SQL BigQuery Agent",
        page_icon="🔍",
        layout="wide",
    )

    st.title("SQL BigQuery Agent")
    st.caption(
        "Ask a business question. The model writes the SQL but is never "
        "trusted with it — six deterministic stages prove the query safe "
        "before it runs, and every figure in the written answer is checked "
        "against the data."
    )

    with st.sidebar:
        st.subheader("Try one of these")
        for question in EXAMPLE_QUESTIONS:
            if st.button(question, width="stretch"):
                st.session_state["question"] = question

        st.divider()
        st.caption(
            "Every query is read-only, restricted to one allowlisted "
            "dataset, row-capped, and cost-checked with a BigQuery dry run "
            "before execution."
        )

    question = st.text_input(
        "Question",
        value=st.session_state.get("question", ""),
        placeholder="What were total net sales by month in 2025?",
    )

    if not st.button("Ask", type="primary") and not question:
        return

    if not question.strip():
        st.info("Enter a question to begin.")
        return

    with st.spinner("Grounding in the schema, writing SQL, checking it…"):
        record = get_agent().run(question)

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

    analysis_text = record.get("analysis", {}).get("business_analysis")
    if analysis_text:
        st.subheader("Analysis")
        st.text(analysis_text)

    render_grounding(record)

    repairs = record.get("sql", {}).get("repair_history") or []
    if repairs:
        with st.expander(f"Repair attempts ({len(repairs)})"):
            for entry in repairs:
                st.markdown(
                    f"**Attempt {entry.get('repair_attempt')}** — failed "
                    f"at `{entry.get('failure_stage')}`"
                )
                st.caption(entry.get("error_message", ""))
                st.code(entry.get("repaired_sql", ""), language="sql")

    with st.expander("Trace and run record"):
        render_trace(record)
        st.json(record, expanded=False)


if __name__ == "__main__":
    main()
