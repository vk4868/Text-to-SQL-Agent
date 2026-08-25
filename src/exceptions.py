from google.cloud.bigquery._job_helpers import job_config_with_defaults
class QueryExecutionTimeoutError(Exception):
    """Raised when a bigquery job exceeds the allowed execution time"""

    def __init__(
        self,
        *,
        job_id: str,
        timeout_seconds: int,
        cancel_requested: bool,
    ) -> None:
        self.job_id = job_id
        self.timeout_seconds = timeout_seconds
        self.cancel_requested = cancel_requested

        message = (
            f"Bigquery job '{job_id}' exceeded the"
            f"{timeout_seconds}-second timeout."
            f"Cancellation requested: {cancel_requested}"
            
        )
        super().__init__(message)


class LLMProviderError(Exception):
    """Raised when an external or local LLM provider call fails"""