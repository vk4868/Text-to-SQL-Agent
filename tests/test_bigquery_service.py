"""BigQueryService against a fake client — no credentials, no network.

BigQueryService accepts an injected client, so its metadata handling and
its call efficiency can both be tested offline.
"""

from typing import Any

import pytest

from src.bigquery_service import BigQueryService
from src.schema_provider import SchemaProvider


class FakeField:
    def __init__(
        self,
        name: str,
        field_type: str,
        mode: str,
        description: str | None = None,
    ) -> None:
        self.name = name
        self.field_type = field_type
        self.mode = mode
        self.description = description


class FakeTimePartitioning:
    def __init__(self, field: str, type_: str = "DAY") -> None:
        self.field = field
        self.type_ = type_


class FakeTable:
    def __init__(
        self,
        table_id: str,
        *,
        schema: list[FakeField] | None = None,
        time_partitioning: FakeTimePartitioning | None = None,
        clustering_fields: list[str] | None = None,
        description: str | None = None,
        num_rows: int = 0,
    ) -> None:
        self.table_id = table_id
        self.schema = schema or []
        self.time_partitioning = time_partitioning
        self.clustering_fields = clustering_fields
        self.description = description
        self.table_type = "TABLE"
        self.num_rows = num_rows
        self.num_bytes = num_rows * 100
        self.require_partition_filter = False

    def __getattr__(self, name: str) -> Any:
        """Fail loudly if production code uses a deprecated attribute.

        `table.partitioning_type` is deprecated in google-cloud-bigquery;
        this fake has no such attribute, so any use of it raises here.
        """

        raise AttributeError(
            f"FakeTable has no attribute '{name}'. "
            "Production code must not rely on it."
        )


class FakeClient:
    """Counts metadata calls so the N+1 fix can be asserted."""

    def __init__(self, tables: list[FakeTable]) -> None:
        self.tables = {table.table_id: table for table in tables}
        self.list_tables_calls = 0
        self.get_table_calls: list[str] = []

    def list_tables(self, dataset_path: str) -> list[FakeTable]:
        self.list_tables_calls += 1
        return list(self.tables.values())

    def get_table(self, table_path: str) -> FakeTable:
        self.get_table_calls.append(table_path)
        table_id = table_path.rsplit(".", 1)[-1]
        return self.tables[table_id]


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient(
        [
            FakeTable(
                "fact_sales",
                schema=[
                    FakeField(
                        "sale_id",
                        "STRING",
                        "REQUIRED",
                        "Primary key.",
                    ),
                    FakeField("net_revenue", "NUMERIC", "REQUIRED"),
                ],
                time_partitioning=FakeTimePartitioning("sale_date"),
                clustering_fields=["customer_id", "product_id"],
                description="One row per transaction line.",
                num_rows=1260,
            ),
            FakeTable(
                "dim_customers",
                schema=[FakeField("customer_id", "STRING", "REQUIRED")],
                num_rows=200,
            ),
            FakeTable(
                "dim_products",
                schema=[FakeField("product_id", "STRING", "REQUIRED")],
                num_rows=40,
            ),
        ]
    )


@pytest.fixture
def service(fake_client: FakeClient) -> BigQueryService:
    return BigQueryService(
        project_id="test-project",
        dataset_id="test_dataset",
        client=fake_client,
    )


def test_dataset_path_is_project_and_dataset(
    service: BigQueryService,
) -> None:
    assert service.dataset_path == "test-project.test_dataset"


def test_table_names_are_sorted(service: BigQueryService) -> None:
    assert service.list_table_names() == [
        "dim_customers",
        "dim_products",
        "fact_sales",
    ]


def test_table_schema_reports_every_column(
    service: BigQueryService,
) -> None:
    columns = service.get_table_schema("fact_sales")

    assert columns == [
        {
            "name": "sale_id",
            "type": "STRING",
            "mode": "REQUIRED",
            "description": "Primary key.",
        },
        {
            "name": "net_revenue",
            "type": "NUMERIC",
            "mode": "REQUIRED",
            "description": None,
        },
    ]


def test_partition_metadata_uses_the_supported_attribute(
    service: BigQueryService,
) -> None:
    """`time_partitioning.type_`, not the deprecated `partitioning_type`."""

    metadata = service.get_table_metadata("fact_sales")

    assert metadata["partition_field"] == "sale_date"
    assert metadata["partition_type"] == "DAY"


def test_reading_metadata_emits_no_deprecation_warning(
    service: BigQueryService,
    recwarn: pytest.WarningsRecorder,
) -> None:
    service.get_table_metadata("fact_sales")

    assert [
        warning
        for warning in recwarn
        if issubclass(warning.category, PendingDeprecationWarning)
    ] == []


def test_unpartitioned_table_reports_no_partition(
    service: BigQueryService,
) -> None:
    metadata = service.get_table_metadata("dim_products")

    assert metadata["partition_field"] is None
    assert metadata["partition_type"] is None
    assert metadata["clustering_fields"] == []


def test_metadata_reports_row_counts_and_description(
    service: BigQueryService,
) -> None:
    metadata = service.get_table_metadata("fact_sales")

    assert metadata["table_name"] == "fact_sales"
    assert metadata["row_count"] == 1260
    assert metadata["description"] == "One row per transaction line."
    assert metadata["clustering_fields"] == [
        "customer_id",
        "product_id",
    ]


@pytest.mark.parametrize(
    "method_name",
    ["get_table_schema", "get_table_metadata"],
)
def test_unknown_table_is_rejected_with_a_readable_message(
    service: BigQueryService,
    method_name: str,
) -> None:
    with pytest.raises(ValueError) as error:
        getattr(service, method_name)("salaries")

    message = str(error.value)

    assert "does not exist in dataset" in message
    assert "indataset" not in message


def test_dataset_structure_covers_every_table(
    service: BigQueryService,
) -> None:
    structure = service.get_dataset_structure()

    assert [
        table["metadata"]["table_name"]  # type: ignore[index]
        for table in structure
    ] == ["dim_customers", "dim_products", "fact_sales"]

    assert all(
        isinstance(table["columns"], list) for table in structure
    )


def test_dataset_structure_lists_the_dataset_once(
    service: BigQueryService,
    fake_client: FakeClient,
) -> None:
    """The N+1 fix: one listing plus one fetch per table."""

    service.get_dataset_structure()

    assert fake_client.list_tables_calls == 1
    assert len(fake_client.get_table_calls) == 3


def test_schema_provider_builds_a_document_from_one_listing(
    service: BigQueryService,
    fake_client: FakeClient,
) -> None:
    provider = SchemaProvider(
        bigquery_service=service,
        relationships=[
            "fact_sales.customer_id = dim_customers.customer_id"
        ],
    )

    document = provider.get_schema_document()

    assert "DATASET: test-project.test_dataset" in document
    assert "TABLE: fact_sales" in document
    assert "PARTITIONED BY: sale_date (DAY)" in document
    assert "RELATIONSHIPS:" in document
    assert fake_client.list_tables_calls == 1
