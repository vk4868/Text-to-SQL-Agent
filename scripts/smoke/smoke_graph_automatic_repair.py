from unittest.mock import patch

from src.bigquery_service import BigQueryService
from src.graph.builder import build_sql_repair_graph
from scripts.smoke._wiring import build_live_nodes
from src.graph.state import AgentState
from src.llm.base import LLMResponse
from src.sql_generator import SQLGenerationResult


def main() -> None:
    bigquery_service = BigQueryService()

    nodes = build_live_nodes(bigquery_service=bigquery_service)

    graph = build_sql_repair_graph(
        nodes=nodes,
    )

    question = (
        "What were total net sales "
        "by month in 2025?"
    )

    bad_sql = f"""
        SELECT
            FORMAT_DATE(
                '%Y-%m',
                sale_date
            ) AS sales_month,
            SUM(revenue) AS total_sales
        FROM
            `{bigquery_service.dataset_path}.fact_sales`
        WHERE
            sale_date >= DATE '2025-01-01'
            AND sale_date < DATE '2026-01-01'
        GROUP BY
            sales_month
        ORDER BY
            sales_month
    """

    fake_generation_result = SQLGenerationResult(
        question=question,
        sql=bad_sql,
        raw_model_output=bad_sql,
        llm_response=LLMResponse(
            text=bad_sql,
            model_name="forced-bad-sql-test",
        ),
    )

    initial_state: AgentState = {
        "question": question,
        "repair_attempts": 0,
        "repair_history": [],
    }

    with patch.object(
        nodes.sql_generator,
        "generate",
        return_value=fake_generation_result,
    ):
        final_state = graph.invoke(
            initial_state
        )

    print("Original generated SQL:")
    print(final_state.get("generated_sql"))

    print("\nFinal SQL:")
    print(final_state.get("final_sql"))

    print("\nRepair attempts:")
    print(final_state.get("repair_attempts"))

    print("\nRepair history:")

    for repair in final_state.get(
        "repair_history",
        [],
    ):
        print("-" * 70)
        print(
            "Attempt:",
            repair.get("repair_attempt"),
        )
        print(
            "Failure stage:",
            repair.get("failure_stage"),
        )
        print(
            "Error:",
            repair.get("error_message"),
        )
        print(
            "Failed SQL:",
        )
        print(
            repair.get("failed_sql")
        )
        print(
            "Repaired SQL:",
        )
        print(
            repair.get("repaired_sql")
        )

    execution_result = final_state.get(
        "execution_result"
    )

    if isinstance(execution_result, dict):
        print("\nFinal execution status:")
        print(
            execution_result.get("status")
        )

        print("\nFinal execution stage:")
        print(
            execution_result.get("stage")
        )

        print("\nRows returned:")
        print(
            execution_result.get("row_count")
        )

    print("\nGraph error stage:")
    print(final_state.get("error_stage"))

    print("\nGraph error message:")
    print(final_state.get("error_message"))


if __name__ == "__main__":
    main()