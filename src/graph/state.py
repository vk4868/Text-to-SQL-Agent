import operator
from typing import Annotated, Any, TypedDict


class AgentState(TypedDict, total=False):
    """Shared state for the word to insights langgraph workflow.

    The four Annotated fields are accumulators: LangGraph merges each node's
    return value into the state using operator.add, so a node contributes only
    the NEW entries it produced, never the whole list. Returning the full list
    from a node would append it to what is already there and duplicate every
    prior entry.
    """

    # --- run identity and timing -------------------------------------------
    graph_run_id: str
    run_started_at: float

    # --- the question and its grounding ------------------------------------
    question: str
    schema_document: str

    # --- SQL as it evolves through generation and repair --------------------
    generated_sql: str
    current_sql: str
    final_sql: str

    execution_result: dict[str, Any]

    repair_attempts: int

    # --- outcome -----------------------------------------------------------
    status: str
    terminal_stage: str
    error_stage: str
    error_message: str

    business_analysis: str
    analysis_metadata: dict[str, Any]

    run_record: dict[str, Any]

    # --- accumulators (see the class docstring) ----------------------------
    node_trace: Annotated[list[dict[str, Any]], operator.add]
    repair_history: Annotated[list[dict[str, Any]], operator.add]
    llm_calls: Annotated[list[dict[str, Any]], operator.add]
    sql_execution_run_ids: Annotated[list[str], operator.add]


#: Keys whose values accumulate across nodes rather than being overwritten.
ACCUMULATOR_KEYS = (
    "node_trace",
    "repair_history",
    "llm_calls",
    "sql_execution_run_ids",
)
