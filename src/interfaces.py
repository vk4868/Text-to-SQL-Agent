from typing import Protocol


class BigQueryReader(Protocol):
    """Define the read-only BigQuery surface the agent depends on.

    Everything above the data layer depends on this Protocol rather than on
    BigQueryService itself, so a fake can be substituted without subclassing
    a class whose constructor builds a live client.
    """

    project_id: str
    dataset_id: str

    @property
    def dataset_path(self) -> str:
        """Return the fully qualified project and dataset name."""
        ...

    def list_table_names(self) -> list[str]:
        """Return all table names in the configured dataset."""
        ...

    def get_dataset_structure(self) -> list[dict[str, object]]:
        """Return the metadata and columns for every table in the dataset."""
        ...

    def dry_run_query(self, sql: str) -> dict[str, object]:
        """Validate SQL in BigQuery and estimate bytes processed."""
        ...

    def run_query(
        self,
        sql: str,
        maximum_bytes_billed: int | None = None,
        max_result_rows: int | None = None,
        query_timeout_seconds: int | None = None,
    ) -> dict[str, object]:
        """Execute a BigQuery SQL query and return rows and job metadata."""
        ...
