"""A fake google.cloud.bigquery.Client.

FakeBigQueryService replaces the whole service; this replaces only the client
underneath it, so BigQueryService's own logic — row mapping, truncation
accounting, timeout handling, job configuration — can be exercised offline.
"""

from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any


class FakeRow:
    """Stand in for a BigQuery Row, which exposes .items()."""

    def __init__(self, values: dict[str, Any]) -> None:
        self._values = values

    def items(self):
        return self._values.items()


class FakeRowIterator:
    """Stand in for a RowIterator, which is iterable and knows total_rows."""

    def __init__(
        self,
        rows: list[dict[str, Any]],
        total_rows: int | None = None,
    ) -> None:
        self._rows = rows
        self.total_rows = (
            total_rows if total_rows is not None else len(rows)
        )

    def __iter__(self):
        return iter(FakeRow(row) for row in self._rows)


class FakeQueryJob:
    """Stand in for a QueryJob."""

    def __init__(
        self,
        *,
        rows: list[dict[str, Any]],
        total_rows: int | None = None,
        job_id: str = "fake-job-1",
        statement_type: str = "SELECT",
        total_bytes_processed: int = 24_010,
        total_bytes_billed: int = 24_010,
        cache_hit: bool = False,
        result_error: Exception | None = None,
        cancel_returns: bool = True,
    ) -> None:
        self._rows = rows
        self._total_rows = total_rows
        self.job_id = job_id
        self.statement_type = statement_type
        self.total_bytes_processed = total_bytes_processed
        self.total_bytes_billed = total_bytes_billed
        self.cache_hit = cache_hit

        self._result_error = result_error
        self._cancel_returns = cancel_returns

        self.cancel_called = False
        self.result_calls: list[dict[str, Any]] = []

    def result(self, max_results=None, timeout=None):
        self.result_calls.append(
            {"max_results": max_results, "timeout": timeout}
        )

        if self._result_error is not None:
            raise self._result_error

        rows = self._rows
        if max_results is not None:
            rows = rows[:max_results]

        return FakeRowIterator(rows, total_rows=self._total_rows)

    def cancel(self) -> bool:
        self.cancel_called = True
        return self._cancel_returns


class FakeTable:
    def __init__(
        self,
        table_id: str,
        *,
        schema: list[Any] | None = None,
        **attributes: Any,
    ) -> None:
        self.table_id = table_id
        self.schema = schema or []

        defaults = {
            "table_type": "TABLE",
            "description": None,
            "num_rows": 0,
            "num_bytes": 0,
            "time_partitioning": None,
            "partitioning_type": None,
            "require_partition_filter": None,
            "clustering_fields": None,
        }
        defaults.update(attributes)

        for name, value in defaults.items():
            setattr(self, name, value)


class FakeBigQueryClient:
    """Record the calls BigQueryService makes and return canned jobs."""

    def __init__(
        self,
        *,
        rows: list[dict[str, Any]] | None = None,
        total_rows: int | None = None,
        tables: list[str] | None = None,
        table_objects: dict[str, FakeTable] | None = None,
        result_error: Exception | None = None,
        estimated_bytes: int = 24_010,
    ) -> None:
        self._rows = rows if rows is not None else [{"value": 1}]
        self._total_rows = total_rows
        self._tables = tables if tables is not None else ["fact_sales"]
        self._table_objects = table_objects or {}
        self._result_error = result_error
        self._estimated_bytes = estimated_bytes

        self.query_calls: list[dict[str, Any]] = []
        self.last_job: FakeQueryJob | None = None

    def list_tables(self, dataset_path: str):
        return [FakeTable(name) for name in self._tables]

    def get_table(self, table_path: str) -> FakeTable:
        name = table_path.rsplit(".", 1)[-1]
        if name in self._table_objects:
            return self._table_objects[name]
        return FakeTable(name)

    def query(self, sql: str, location=None, job_config=None):
        self.query_calls.append(
            {"sql": sql, "location": location, "job_config": job_config}
        )

        dry_run = bool(getattr(job_config, "dry_run", False))

        job = FakeQueryJob(
            rows=[] if dry_run else self._rows,
            total_rows=None if dry_run else self._total_rows,
            total_bytes_processed=self._estimated_bytes,
            total_bytes_billed=self._estimated_bytes,
            result_error=None if dry_run else self._result_error,
        )

        self.last_job = job
        return job
