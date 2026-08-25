from src.graph.nodes import InsightsGraphNodes


def main() -> None:
    successful_schema_state = {
        "question": "What were sales by month?",
        "schema_document": "DATASET: example",
    }

    failed_schema_state = {
        "question": "What were sales by month?",
        "error_stage": "schema_retrieval",
        "error_message": "Simulated schema failure",
    }

    successful_generation_state = {
        "question": "What were sales by month?",
        "schema_document": "DATASET: example",
        "generated_sql": "SELECT 1",
        "current_sql": "SELECT 1",
    }

    failed_generation_state = {
        "question": "What were sales by month?",
        "schema_document": "DATASET: example",
        "error_stage": "sql_generation",
        "error_message": "Simulated LLM failure",
    }

    print(
        "Schema success:",
        InsightsGraphNodes.route_after_schema(
            successful_schema_state,
        ),
    )

    print(
        "Schema failure:",
        InsightsGraphNodes.route_after_schema(
            failed_schema_state,
        ),
    )

    print(
        "Generation success:",
        InsightsGraphNodes.route_after_generation(
            successful_generation_state,
        ),
    )

    print(
        "Generation failure:",
        InsightsGraphNodes.route_after_generation(
            failed_generation_state,
        ),
    )


if __name__ == "__main__":
    main()