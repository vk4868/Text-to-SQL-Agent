"""Cover the injection and configuration seams every later phase leans on.

These exist because a Gate 0 audit deleted the BigQueryService `client=` seam
and rebound SQLExecutionPipeline's caps as import-time defaults, and the whole
suite stayed green. Untested seams are seams that quietly rot.
"""

from concurrent.futures import TimeoutError as FuturesTimeoutError
import inspect

import pytest

from src import config
from src.bigquery_service import BigQueryService
from src.exceptions import QueryExecutionTimeoutError
from src.interfaces import BigQueryReader
from src.schema_provider import SchemaProvider
from src.sql_execution_pipeline import SQLExecutionPipeline
from tests.fakes.bigquery import FakeBigQueryService
from tests.fakes.client import FakeBigQueryClient, FakeTable


class TestClientInjectionSeam:
    """BigQueryService must accept an injected client (0.5)."""

    def test_injected_client_is_used_verbatim(self):
        client = FakeBigQueryClient()

        service = BigQueryService(client=client)

        assert service.client is client

    def test_constructing_without_a_client_would_build_a_live_one(self):
        """The autouse guard makes a live build raise, which proves the
        fallback path is still wired to bigquery.Client."""

        with pytest.raises(RuntimeError, match="live client"):
            BigQueryService()

    def test_run_query_maps_rows_through_the_injected_client(self):
        client = FakeBigQueryClient(
            rows=[{"month": "2025-01", "total": 10}, {"month": "2025-02", "total": 20}]
        )
        service = BigQueryService(client=client, project_id="p", dataset_id="d")

        result = service.run_query("SELECT 1")

        assert result["rows"] == [
            {"month": "2025-01", "total": 10},
            {"month": "2025-02", "total": 20},
        ]
        assert result["row_count"] == 2
        assert result["job_id"] == "fake-job-1"
        assert result["cache_hit"] is False

    def test_run_query_passes_the_caps_to_bigquery(self):
        client = FakeBigQueryClient()
        service = BigQueryService(client=client)

        service.run_query(
            "SELECT 1",
            maximum_bytes_billed=555,
            max_result_rows=10,
            query_timeout_seconds=7,
        )

        job_config = client.query_calls[0]["job_config"]

        assert job_config.maximum_bytes_billed == 555
        # The client normalises job_timeout_ms to its API string form.
        assert int(job_config.job_timeout_ms) == 7000
        assert client.last_job is not None
        assert client.last_job.result_calls[0] == {
            "max_results": 10,
            "timeout": 7,
        }

    def test_client_side_truncation_is_reported(self):
        client = FakeBigQueryClient(
            rows=[{"n": i} for i in range(5)], total_rows=100
        )
        service = BigQueryService(client=client)

        result = service.run_query("SELECT 1", max_result_rows=5)

        assert result["row_count"] == 5
        assert result["total_result_rows"] == 100
        assert result["result_truncated_by_client"] is True

    def test_timeout_cancels_the_job_and_raises(self):
        client = FakeBigQueryClient(result_error=FuturesTimeoutError())
        service = BigQueryService(client=client)

        with pytest.raises(QueryExecutionTimeoutError) as caught:
            service.run_query("SELECT 1", query_timeout_seconds=3)

        assert caught.value.timeout_seconds == 3
        assert caught.value.job_id == "fake-job-1"
        assert caught.value.cancel_requested is True
        assert client.last_job is not None
        assert client.last_job.cancel_called is True

    def test_dry_run_reports_estimated_bytes_without_executing(self):
        client = FakeBigQueryClient(estimated_bytes=98_765)
        service = BigQueryService(client=client)

        result = service.dry_run_query("SELECT 1")

        assert result["estimated_bytes_processed"] == 98_765
        assert client.query_calls[0]["job_config"].dry_run is True

    def test_unknown_table_is_rejected_before_any_api_call(self):
        client = FakeBigQueryClient(tables=["fact_sales"])
        service = BigQueryService(client=client, project_id="p", dataset_id="d")

        with pytest.raises(ValueError, match="does not exist in dataset"):
            service.get_table_schema("secret_table")


class TestCallTimeConfigResolution:
    """Caps and identifiers must resolve at call time, not import time (0.3)."""

    def test_bigquery_service_reads_config_when_constructed(
        self, monkeypatch
    ):
        monkeypatch.setattr(config, "PROJECT_ID", "late-project")
        monkeypatch.setattr(config, "DATASET_ID", "late-dataset")
        monkeypatch.setattr(config, "BIGQUERY_LOCATION", "late-location")

        service = BigQueryService(client=FakeBigQueryClient())

        assert service.project_id == "late-project"
        assert service.dataset_id == "late-dataset"
        assert service.location == "late-location"
        assert service.dataset_path == "late-project.late-dataset"

    def test_execution_pipeline_reads_config_when_constructed(
        self, monkeypatch, fake_bq
    ):
        monkeypatch.setattr(config, "MAX_QUERY_BYTES", 111)
        monkeypatch.setattr(config, "MAX_RESULT_ROWS", 222)
        monkeypatch.setattr(config, "QUERY_TIMEOUT_SECONDS", 333)

        pipeline = SQLExecutionPipeline(bigquery_service=fake_bq)

        assert pipeline.max_query_bytes == 111
        assert pipeline.max_result_rows == 222
        assert pipeline.query_timeout_seconds == 333

    def test_schema_provider_reads_cache_ttl_when_constructed(
        self, monkeypatch, fake_bq
    ):
        monkeypatch.setattr(config, "SCHEMA_CACHE_TTL_SECONDS", 42.0)

        provider = SchemaProvider(
            bigquery_service=fake_bq, relationships=[]
        )

        assert provider.cache_ttl_seconds == 42.0

    def test_explicit_arguments_still_win_over_config(
        self, monkeypatch, fake_bq
    ):
        monkeypatch.setattr(config, "MAX_RESULT_ROWS", 222)

        pipeline = SQLExecutionPipeline(
            bigquery_service=fake_bq, max_result_rows=9
        )

        assert pipeline.max_result_rows == 9

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"max_query_bytes": 0},
            {"max_result_rows": 0},
            {"query_timeout_seconds": -1},
        ],
    )
    def test_non_positive_caps_are_rejected(self, fake_bq, kwargs):
        with pytest.raises(ValueError, match="greater than zero"):
            SQLExecutionPipeline(bigquery_service=fake_bq, **kwargs)


class TestFakeConformsToTheProtocol:
    """The fake must not silently drift from the real service (0.5).

    A plain Protocol is not enforced at runtime, so without this the fake
    could grow a different signature and every test would keep passing while
    testing something the production code cannot actually do.
    """

    PROTOCOL_METHODS = [
        "list_table_names",
        "get_dataset_structure",
        "dry_run_query",
        "run_query",
    ]

    @pytest.mark.parametrize("method_name", PROTOCOL_METHODS)
    def test_fake_signature_matches_the_real_service(self, method_name):
        real = inspect.signature(getattr(BigQueryService, method_name))
        fake = inspect.signature(getattr(FakeBigQueryService, method_name))

        assert fake.parameters.keys() == real.parameters.keys(), (
            f"FakeBigQueryService.{method_name} has drifted from "
            f"BigQueryService.{method_name}"
        )

    @pytest.mark.parametrize("method_name", PROTOCOL_METHODS)
    def test_protocol_declares_what_the_real_service_provides(
        self, method_name
    ):
        assert hasattr(BigQueryReader, method_name)
        assert hasattr(BigQueryService, method_name)
        assert hasattr(FakeBigQueryService, method_name)

    def test_both_expose_dataset_path_as_a_property(self):
        assert isinstance(
            inspect.getattr_static(BigQueryService, "dataset_path"), property
        )
        assert isinstance(
            inspect.getattr_static(FakeBigQueryService, "dataset_path"),
            property,
        )

    def test_the_real_service_satisfies_the_pipeline_contract(self):
        """Construct the pipeline against a real service backed by a fake
        client, proving the production type is accepted where the fake is."""

        service = BigQueryService(client=FakeBigQueryClient())

        pipeline = SQLExecutionPipeline(bigquery_service=service)

        assert pipeline.bigquery_service is service


class TestSchemaProviderAgainstRealService:
    """SchemaProvider must work with the real service, not only the fake."""

    def test_document_is_built_from_the_real_service(self):
        table = FakeTable(
            "fact_sales",
            schema=[
                type(
                    "Field",
                    (),
                    {
                        "name": "net_revenue",
                        "field_type": "NUMERIC",
                        "mode": "REQUIRED",
                        "description": None,
                    },
                )()
            ],
            num_rows=1260,
        )
        client = FakeBigQueryClient(
            tables=["fact_sales"], table_objects={"fact_sales": table}
        )
        service = BigQueryService(
            client=client, project_id="p", dataset_id="d"
        )

        provider = SchemaProvider(
            bigquery_service=service,
            relationships=["fact_sales.x = dim.y"],
        )

        document = provider.get_schema_document()

        assert "DATASET: p.d" in document
        assert "TABLE: fact_sales" in document
        assert "net_revenue NUMERIC REQUIRED" in document
        assert "fact_sales.x = dim.y" in document
