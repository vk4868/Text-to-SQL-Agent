# Annotations are evaluated lazily so that `bigquery.Client | None` in the
# constructor signature does not need bigquery.Client to be a real class at
# import time.
from __future__ import annotations

from google.cloud import bigquery
from concurrent.futures import TimeoutError as FuturesTimeoutError
from src.exceptions import QueryExecutionTimeoutError

from src import config
from src.interfaces import BigQueryClientLike

class BigQueryService:
    """to provide reusable access to the project's bigquery dataset"""

    def __init__(
        self,
        project_id: str | None = None,
        dataset_id: str | None = None,
        location: str | None = None,
        client: BigQueryClientLike | None = None,
    ) -> None:
        # Resolved at call time rather than captured as import-time defaults,
        # so tests and callers can redirect the target project.
        self.project_id = project_id or config.PROJECT_ID
        self.dataset_id = dataset_id or config.DATASET_ID
        self.location = location or config.BIGQUERY_LOCATION

        # Injection seam: pass a fake client to exercise this class offline.
        self.client = client or bigquery.Client(
            project=self.project_id,
            location=self.location
        )
    
    @property
    def dataset_path(self) -> str:
        """Return the fully qualified project and dataset name"""
        return f"{self.project_id}.{self.dataset_id}"

    def list_table_names(self) -> list[str]:
        """Return all table names in the configured dataset"""
        tables = self.client.list_tables(self.dataset_path)
        
        return sorted(table.table_id for table in tables)

    def get_table_schema(self,table_name:str) -> list[dict[str, str | None]]:
        """Return column metadata for one table in the configured dataset"""
        available_tables = set(self.list_table_names())

        if table_name not in available_tables:
            raise ValueError(
                f"Table '{table_name}' does not exist in "
                f"dataset '{self.dataset_path}'."
            )
        table_path=f"{self.dataset_path}.{table_name}"
        table = self.client.get_table(table_path)

        return [
            {
                "name":field.name,
                "type":field.field_type,
                "mode":field.mode,
                "description": field.description
            }
            for field in table.schema
        ]
    def get_table_metadata(
        self,table_name:str,
    ) -> dict[str, str | int | bool | list[str] | None]:
        """Returns table level metadata for one bigquery table"""

        available_tables = set(self.list_table_names())

        if table_name not in available_tables:
            raise ValueError(
                f"Table '{table_name}' does not exist in "
                f"dataset '{self.dataset_path}'."
            )

        table_path = f"{self.dataset_path}.{table_name}"
        table = self.client.get_table(table_path)

        partition_field = None

        if table.time_partitioning is not None:
            partition_field = table.time_partitioning.field
        
        return {
        "table_name": table_name,
        "table_type": table.table_type,
        "description": table.description,
        "row_count": table.num_rows,
        "size_bytes": table.num_bytes,
        "partition_field": partition_field,
        "partition_type": table.partitioning_type,
        "require_partition_filter": table.require_partition_filter,
        "clustering_fields": table.clustering_fields or [],
    }

    def get_dataset_structure(self) -> list[dict[str, object]]:
        """Return the metadata and columns for every table in the dataset"""

        dataset_structure: list[dict[str, object]] = []

        for table_name in self.list_table_names():
            table_details: dict[str, object] = {
                "metadata": self.get_table_metadata(table_name),
                "columns": self.get_table_schema(table_name),
            }
            dataset_structure.append(table_details)

        return dataset_structure

    def run_query(self,sql:str, maximum_bytes_billed: int | None=None, max_result_rows: int | None = None,
    query_timeout_seconds: int | None = None) -> dict[str,object]:
        """Execute a BigQuery SQL query and return rows and job metadata"""
        job_config=bigquery.QueryJobConfig(
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
        "cache_hit": query_job.cache_hit,}
    def dry_run_query(self, sql:str) -> dict[str,object]:
        """Validate SQL in bigquery and estimate bytes processed"""

        job_config = bigquery.QueryJobConfig(
            dry_run=True,
            use_query_cache=False,
            use_legacy_sql=False,
        )
        query_job = self.client.query(
            sql,
            location=self.location,
            job_config=job_config
        )
        return{
            "estimated_bytes_processed": (
                query_job.total_bytes_processed or 0
            ),
            "statement_type": query_job.statement_type
        }
