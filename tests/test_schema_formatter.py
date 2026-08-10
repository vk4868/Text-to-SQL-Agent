"""The schema document handed to the LLM."""

from src.schema_formatter import format_dataset_structure

DATASET_PATH = "test-project.test_dataset"

FACT_SALES = {
    "metadata": {
        "table_name": "fact_sales",
        "table_type": "TABLE",
        "description": "One row per transaction line.",
        "row_count": 1260,
        "size_bytes": 90_000,
        "partition_field": "sale_date",
        "partition_type": "DAY",
        "require_partition_filter": False,
        "clustering_fields": ["customer_id", "product_id"],
    },
    "columns": [
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
    ],
}

MINIMAL_TABLE = {
    "metadata": {
        "table_name": "dim_products",
        "table_type": "TABLE",
        "description": None,
        "row_count": 40,
        "size_bytes": 2_000,
        "partition_field": None,
        "partition_type": None,
        "require_partition_filter": None,
        "clustering_fields": [],
    },
    "columns": [
        {
            "name": "product_id",
            "type": "STRING",
            "mode": "REQUIRED",
            "description": None,
        }
    ],
}


def test_dataset_path_is_the_first_line() -> None:
    document = format_dataset_structure(DATASET_PATH, [FACT_SALES])

    assert document.splitlines()[0] == f"DATASET: {DATASET_PATH}"


def test_table_name_row_count_and_type_are_included() -> None:
    document = format_dataset_structure(DATASET_PATH, [FACT_SALES])

    assert "TABLE: fact_sales" in document
    assert "ROW COUNT: 1260" in document
    assert "TABLE TYPE: TABLE" in document


def test_columns_carry_name_type_mode_and_description() -> None:
    document = format_dataset_structure(DATASET_PATH, [FACT_SALES])

    assert "- sale_id STRING REQUIRED — Primary key." in document
    assert "- net_revenue NUMERIC REQUIRED" in document


def test_partition_and_clustering_hints_are_included() -> None:
    """These hints are what let the model write cheap queries."""

    document = format_dataset_structure(DATASET_PATH, [FACT_SALES])

    assert "PARTITIONED BY: sale_date (DAY)" in document
    assert "CLUSTERED BY: customer_id, product_id" in document


def test_optional_lines_are_omitted_when_absent() -> None:
    document = format_dataset_structure(DATASET_PATH, [MINIMAL_TABLE])

    assert "DESCRIPTION:" not in document
    assert "PARTITIONED BY:" not in document
    assert "CLUSTERED BY:" not in document


def test_relationships_are_appended_when_supplied() -> None:
    document = format_dataset_structure(
        DATASET_PATH,
        [FACT_SALES],
        relationships=[
            "fact_sales.customer_id = dim_customers.customer_id"
        ],
    )

    assert "RELATIONSHIPS:" in document
    assert (
        "- fact_sales.customer_id = dim_customers.customer_id"
        in document
    )


def test_relationships_section_is_omitted_when_empty() -> None:
    document = format_dataset_structure(
        DATASET_PATH,
        [FACT_SALES],
        relationships=[],
    )

    assert "RELATIONSHIPS:" not in document


def test_every_table_appears_in_order() -> None:
    document = format_dataset_structure(
        DATASET_PATH,
        [FACT_SALES, MINIMAL_TABLE],
    )

    assert document.index("TABLE: fact_sales") < document.index(
        "TABLE: dim_products"
    )


def test_an_empty_dataset_still_produces_a_header() -> None:
    document = format_dataset_structure(DATASET_PATH, [])

    assert document == f"DATASET: {DATASET_PATH}"
