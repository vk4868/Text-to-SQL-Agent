from src.observability import (
    configure_application_logger,
    write_jsonl_record,
)


def main() -> None:
    logger = configure_application_logger()

    logger.info("Observability test started.")

    write_jsonl_record(
        {
            "event": "observability_test",
            "component": "sql_execution_pipeline",
            "status": "success",
            "row_count": 5,
            "estimated_bytes_processed": 25000,
        }
    )

    logger.info("Structured JSONL record written.")
    logger.warning("This is a simulated warning.")

    print("Check the logs directory for:")
    print("- agent_run.log")
    print("- runs.jsonl")


if __name__ == "__main__":
    main()