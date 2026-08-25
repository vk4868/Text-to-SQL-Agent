from typing import Any, TypedDict

class AgentState(TypedDict, total=False):
    """Shared state for the word to insights langraph workflow"""

    graph_run_id: str
    node_trace:list[dict[str,Any]]
    question: str

    schema_document: str

    generated_sql:str
    current_sql:str
    final_sql:str

    execution_result: dict[str,Any]

    repair_attempts: int
    repair_history: list[dict[str,Any]]

    business_analysis: str
    analysis_metadata: dict[str,Any]

    error_stage:str
    error_message: str
    