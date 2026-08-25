from concurrent.futures import TimeoutError as FuturesTimeoutError
from unittest.mock import Mock, patch

from src.bigquery_service import BigQueryService
from src.exceptions import QueryExecutionTimeoutError

def main() -> None:
    service = BigQueryService()

    fake_query_job = Mock()
    fake_query_job.job_id = "simulated-job-123"
    fake_query_job.result.side_effect = FuturesTimeoutError()
    fake_query_job.cancel.return_value = True

    with patch.object(
        service.client,
        "query",
        return_value=fake_query_job
    ):
        try:
            service.run_query(
                "SELECT 1",
                query_timeout_seconds=1,
            )

        except QueryExecutionTimeoutError as error:
            print("Timeout handled succesfully")
            print(f"Job ID: {error.job_id}")
            print(
                f"Timeout seconds:"
                f"{error.timeout_seconds}"
            )
            print(f"Message: {error}")
if __name__ == "__main__":
    main()
