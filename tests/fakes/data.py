"""Canned dataset metadata and rows mirroring the real business_insights set.

Shapes match what BigQueryService actually returns, so a fake built on this
data exercises the same code paths as the live service.
"""

from typing import Any

PROJECT_ID = "test-project"
DATASET_ID = "business_insights"
DATASET_PATH = f"{PROJECT_ID}.{DATASET_ID}"

TABLE_NAMES = ["dim_customers", "dim_products", "fact_sales"]


def _column(
    name: str,
    field_type: str,
    mode: str = "NULLABLE",
    description: str | None = None,
) -> dict[str, str | None]:
    return {
        "name": name,
        "type": field_type,
        "mode": mode,
        "description": description,
    }


DATASET_STRUCTURE: list[dict[str, Any]] = [
    {
        "metadata": {
            "table_name": "dim_customers",
            "table_type": "TABLE",
            "description": "One row per customer.",
            "row_count": 200,
            "size_bytes": 20_000,
            "partition_field": None,
            "partition_type": None,
            "require_partition_filter": None,
            "clustering_fields": [],
        },
        "columns": [
            _column("customer_id", "STRING", "REQUIRED", "Primary key."),
            _column("customer_name", "STRING"),
            _column("branch", "STRING"),
            _column("is_member", "BOOLEAN"),
        ],
    },
    {
        "metadata": {
            "table_name": "dim_products",
            "table_type": "TABLE",
            "description": "One row per product.",
            "row_count": 40,
            "size_bytes": 4_000,
            "partition_field": None,
            "partition_type": None,
            "require_partition_filter": None,
            "clustering_fields": [],
        },
        "columns": [
            _column("product_id", "STRING", "REQUIRED", "Primary key."),
            _column("product_name", "STRING"),
            _column("category", "STRING"),
            _column("unit_cost", "NUMERIC"),
        ],
    },
    {
        "metadata": {
            "table_name": "fact_sales",
            "table_type": "TABLE",
            "description": "One row per transaction line.",
            "row_count": 1_260,
            "size_bytes": 180_000,
            "partition_field": "sale_date",
            "partition_type": "DAY",
            "require_partition_filter": False,
            "clustering_fields": ["customer_id", "product_id"],
        },
        "columns": [
            _column("sale_id", "STRING", "REQUIRED", "Primary key."),
            _column("sale_date", "DATE", "REQUIRED"),
            _column("customer_id", "STRING", "REQUIRED"),
            _column("product_id", "STRING", "REQUIRED"),
            _column("quantity", "INTEGER"),
            _column("net_revenue", "NUMERIC", "REQUIRED"),
            _column("profit_amount", "NUMERIC", "REQUIRED"),
        ],
    },
]

# A representative successful result: monthly net sales.
MONTHLY_SALES_ROWS: list[dict[str, Any]] = [
    {"month": "2025-01", "total_net_sales": 41_250.75},
    {"month": "2025-02", "total_net_sales": 38_910.20},
    {"month": "2025-03", "total_net_sales": 44_005.00},
]

RELATIONSHIPS = [
    "fact_sales.customer_id = dim_customers.customer_id",
    "fact_sales.product_id = dim_products.product_id",
]

VALID_SQL = (
    "SELECT FORMAT_DATE('%Y-%m', sale_date) AS month, "
    "SUM(net_revenue) AS total_net_sales "
    f"FROM `{DATASET_PATH}.fact_sales` "
    "WHERE sale_date BETWEEN '2025-01-01' AND '2025-12-31' "
    "GROUP BY month ORDER BY month"
)
