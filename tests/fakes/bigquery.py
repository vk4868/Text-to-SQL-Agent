"""An in-memory stand-in for BigQueryService.

Deliberately not a subclass: BigQueryService.__init__ builds a live client, so
subclassing would drag credentials into every test. This implements the
BigQueryReader Protocol instead, which is the sanctioned seam.
"""

from typing import Any

from tests.fakes import data


class FakeBigQueryService:
    """Serve canned schema metadata and query results."""

    def __init__(
        self,
        *,
        project_id: str = data.PROJECT_ID,
        dataset_id: str = data.DATASET_ID,
        table_names: list[str] | None = None,
        dataset_structure: list[dict[str, Any]] | None = None,
        rows: list[dict[str, Any]] | None = None,
        estimated_bytes: int = 24_010,
        dry_run_error: Exception | None = None,
        run_query_error: Exception | None = None,
        total_result_rows: int | None = None,
    ) -> None:
        self.project_id = project_id
        self.dataset_id = dataset_id

        self._table_names = (
            list(table_names)
            if table_names is not None
            else list(data.TABLE_NAMES)
        )
        self._dataset_structure = (
            dataset_structure
            if dataset_structure is not None
            else data.DATASET_STRUCTURE
        )
        self.rows = rows if rows is not None else list(data.MONTHLY_SALES_ROWS)
        self.estimated_bytes = estimated_bytes

        # Set either of these to make the corresponding stage fail. Both accept
        # a callable taking the SQL, so a fake can fail only the first attempt
        # and let a repaired query through.
        self.dry_run_error = dry_run_error
        self.run_query_error = run_query_error

        self._total_result_rows = total_result_rows

        self.list_table_names_calls = 0
        self.dataset_structure_calls = 0
        self.dry_run_calls: list[str] = []
        self.run_query_calls: list[str] = []

    @property
    def dataset_path(self) -> str:
        return f"{self.project_id}.{self.dataset_id}"

    def list_table_names(self) -> list[str]:
        self.list_table_names_calls += 1
        return list(self._table_names)

    def get_dataset_structure(self) -> list[dict[str, object]]:
        self.dataset_structure_calls += 1
        return self._dataset_structure

    def dry_run_query(self, sql: str) -> dict[str, object]:
        self.dry_run_calls.append(sql)

        error = self._resolve_error(self.dry_run_error, sql)
        if error is not None:
            raise error

        return {
            "estimated_bytes_processed": self.estimated_bytes,
            "statement_type": "SELECT",
        }

    def run_query(
        self,
        sql: str,
        maximum_bytes_billed: int | None = None,
        max_result_rows: int | None = None,
        query_timeout_seconds: int | None = None,
    ) -> dict[str, object]:
        self.run_query_calls.append(sql)

        error = self._resolve_error(self.run_query_error, sql)
        if error is not None:
            raise error

        rows = list(self.rows)
        if max_result_rows is not None:
            rows = rows[:max_result_rows]

        total = (
            self._total_result_rows
            if self._total_result_rows is not None
            else len(rows)
        )

        return {
            "rows": rows,
            "row_count": len(rows),
            "total_result_rows": total,
            "result_truncated_by_client": total > len(rows),
            "job_id": "fake-job-1",
            "statement_type": "SELECT",
            "total_bytes_processed": self.estimated_bytes,
            "total_bytes_billed": self.estimated_bytes,
            "cache_hit": False,
        }

    @staticmethod
    def _resolve_error(error: Any, sql: str) -> Exception | None:
        """Allow an error to be static or decided per-query by a callable."""

        if error is None:
            return None
        if callable(error) and not isinstance(error, BaseException):
            return error(sql)
        return error
