from typing import Any

from google.cloud import bigquery

from concurrent.futures import TimeoutError as FuturesTimeoutError

from src.config import (
    BIGQUERY_LOCATION,
    DATASET_ID,
    PROJECT_ID,
)
from src.exceptions import QueryExecutionTimeoutError


class BigQueryService:
    """to provide reusable access to the project's bigquery dataset"""

    def __init__(
        self,
        project_id: str = PROJECT_ID,
        dataset_id: str = DATASET_ID,
        location: str = BIGQUERY_LOCATION,
        client: Any | None = None,
    ) -> None:
        self.project_id = project_id
        self.dataset_id = dataset_id
        self.location = location

        # A client can be supplied for testing. In normal use the
        # client is created from Application Default Credentials.
        self.client = client or bigquery.Client(
            project=self.project_id,
            location=self.location,
        )

    @property
    def dataset_path(self) -> str:
        """Return the fully qualified project and dataset name"""
        return f"{self.project_id}.{self.dataset_id}"

    def list_table_names(self) -> list[str]:
        """Return all table names in the configured dataset"""
        tables = self.client.list_tables(self.dataset_path)

        return sorted(table.table_id for table in tables)

    def _fetch_table(
        self,
        table_name: str,
        *,
        known_table_names: set[str] | None = None,
    ) -> Any:
        """Return one BigQuery table object after checking it exists.

        `known_table_names` lets a caller that has already listed the
        dataset reuse that listing instead of paying for another
        metadata round trip.
        """

        available_tables = (
            known_table_names
            if known_table_names is not None
            else set(self.list_table_names())
        )

        if table_name not in available_tables:
            raise ValueError(
                f"Table '{table_name}' does not exist in "
                f"dataset '{self.dataset_path}'."
            )

        table_path = f"{self.dataset_path}.{table_name}"

        return self.client.get_table(table_path)

    @staticmethod
    def _build_column_metadata(
        table: Any,
    ) -> list[dict[str, str | None]]:
        """Return per-column metadata for one BigQuery table object."""

        return [
            {
                "name": field.name,
                "type": field.field_type,
                "mode": field.mode,
                "description": field.description,
            }
            for field in table.schema
        ]

    @staticmethod
    def _build_table_metadata(
        table: Any,
        table_name: str,
    ) -> dict[str, str | int | bool | list[str] | None]:
        """Return table-level metadata for one BigQuery table object."""

        partition_field = None
        partition_type = None

        if table.time_partitioning is not None:
            partition_field = table.time_partitioning.field
            partition_type = table.time_partitioning.type_

        return {
            "table_name": table_name,
            "table_type": table.table_type,
            "description": table.description,
            "row_count": table.num_rows,
            "size_bytes": table.num_bytes,
            "partition_field": partition_field,
            "partition_type": partition_type,
            "require_partition_filter": table.require_partition_filter,
            "clustering_fields": table.clustering_fields or [],
        }

    def get_table_schema(
        self,
        table_name: str,
        *,
        known_table_names: set[str] | None = None,
    ) -> list[dict[str, str | None]]:
        """Return column metadata for one table in the configured dataset"""

        table = self._fetch_table(
            table_name,
            known_table_names=known_table_names,
        )

        return self._build_column_metadata(table)

    def get_table_metadata(
        self,
        table_name: str,
        *,
        known_table_names: set[str] | None = None,
    ) -> dict[str, str | int | bool | list[str] | None]:
        """Returns table level metadata for one bigquery table"""

        table = self._fetch_table(
            table_name,
            known_table_names=known_table_names,
        )

        return self._build_table_metadata(table, table_name)

    def get_dataset_structure(self) -> list[dict[str, object]]:
        """Return the metadata and columns for every table in the dataset.

        The dataset is listed once and each table is fetched once, so a
        three-table dataset costs four metadata calls rather than one
        per helper invocation.
        """

        table_names = self.list_table_names()

        dataset_structure: list[dict[str, object]] = []

        for table_name in table_names:
            table = self._fetch_table(
                table_name,
                known_table_names=set(table_names),
            )

            table_details: dict[str, object] = {
                "metadata": self._build_table_metadata(
                    table,
                    table_name,
                ),
                "columns": self._build_column_metadata(table),
            }

            dataset_structure.append(table_details)

        return dataset_structure

    def run_query(
        self,
        sql: str,
        maximum_bytes_billed: int | None = None,
        max_result_rows: int | None = None,
        query_timeout_seconds: int | None = None,
    ) -> dict[str, object]:
        """Execute a BigQuery SQL query and return rows and job metadata"""
        job_config = bigquery.QueryJobConfig(
            use_legacy_sql=False,
        )
        if maximum_bytes_billed is not None:
            job_config.maximum_bytes_billed = maximum_bytes_billed
        if query_timeout_seconds is not None:
            job_config.job_timeout_ms = (
                query_timeout_seconds * 1000
            )
        query_job = self.client.query(
            sql,
            location=self.location,
            job_config=job_config,
        )
        try:
            query_results = query_job.result(
                max_results=max_result_rows,
                timeout=query_timeout_seconds,
            )

        except FuturesTimeoutError as error:
            cancel_requested = query_job.cancel()

            raise QueryExecutionTimeoutError(
                job_id=query_job.job_id or "unknown",
                timeout_seconds=query_timeout_seconds or 0,
                cancel_requested=cancel_requested,
            ) from error

        rows = []

        for row in query_results:
            rows.append(dict(row.items()))
        total_result_rows = query_results.total_rows or 0
        return {
            "rows": rows,
            "row_count": len(rows),
            "total_result_rows": total_result_rows,
            "result_truncated_by_client": (
                total_result_rows > len(rows)
            ),
            "job_id": query_job.job_id,
            "statement_type": query_job.statement_type,
            "total_bytes_processed": (
                query_job.total_bytes_processed
            ),
            "total_bytes_billed": (
                query_job.total_bytes_billed
            ),
            "cache_hit": query_job.cache_hit,
        }

    def dry_run_query(self, sql: str) -> dict[str, object]:
        """Validate SQL in bigquery and estimate bytes processed"""

        job_config = bigquery.QueryJobConfig(
            dry_run=True,
            use_query_cache=False,
            use_legacy_sql=False,
        )
        query_job = self.client.query(
            sql,
            location=self.location,
            job_config=job_config,
        )
        return {
            "estimated_bytes_processed": (
                query_job.total_bytes_processed or 0
            ),
            "statement_type": query_job.statement_type,
        }
