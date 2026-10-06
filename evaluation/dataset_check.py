"""Keyed, row-level comparison of the bundled CSVs against the live tables.

    uv run python -m evaluation.dataset_check --out dataset_check.json

Per-column distinct-value fingerprints cannot see two rows exchanging a value
(same distinct sets, same sums). This hashes whole rows, ordered by primary
key, so any swapped association changes the digest.

Canonical cell text, identical on both sides: strings as-is; NULL/empty -> "";
dates -> ``YYYY-MM-DD``; numerics -> plain decimal with trailing zeros (and a
trailing ".") stripped, so "1933.90" -> "1933.9" and "0.00" -> "0".
ASSUMPTION: BigQuery ``CAST(col AS STRING)`` renders NUMERIC without trailing
zeros and DATE as ISO, which matches that canonicalisation. A FLOAT64 column
may render differently; the check would then report a mismatch, not hide one.

Row text is the cells joined by TAB in CSV header order; the table digest is
SHA-256 over the rows joined by LF, ordered by key ascending (string order).
Every live query goes through ``execution_pipeline.execute``.
"""

import argparse
import csv
import hashlib
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Sequence

from evaluation.manifest import _read_csv_rows

KEYS = {
    "fact_sales": "sale_id",
    "dim_customers": "customer_id",
    "dim_products": "product_id",
}

NUMERIC_COLUMNS = {
    "fact_sales": {
        "quantity", "unit_price", "discount_pct", "gross_revenue",
        "discount_amount", "net_revenue", "tax_amount", "total_price",
        "cost_amount", "profit_amount", "reward_points",
    },
    "dim_customers": set(),
    "dim_products": {"unit_cost", "list_price"},
}


def canonical_numeric(value: str) -> str:
    text = value.strip()
    if text == "":
        return ""
    try:
        number = Decimal(text)
    except InvalidOperation:
        return text
    if number == 0:
        return "0"
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def canonical_cell(table: str, column: str, value: str | None) -> str:
    if value is None or value == "":
        return ""
    if column in NUMERIC_COLUMNS[table]:
        return canonical_numeric(value)
    return value  # dates are ISO in the CSV; strings are as-is


def _header(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        return next(csv.reader(handle))


def csv_table_summary(csv_dir: Path, table: str) -> dict[str, Any]:
    path = Path(csv_dir) / f"{table}.csv"
    columns = _header(path)
    key = KEYS[table]
    rows = _read_csv_rows(path)
    ordered = sorted(rows, key=lambda row: row[key])
    text = "\n".join(
        "\t".join(canonical_cell(table, c, row.get(c)) for c in columns)
        for row in ordered
    )
    return {
        "row_count": len(rows),
        "distinct_keys": len({row[key] for row in rows}),
        "row_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def csv_orphans(csv_dir: Path) -> dict[str, int]:
    sales = _read_csv_rows(Path(csv_dir) / "fact_sales.csv")
    customers = {
        r["customer_id"]
        for r in _read_csv_rows(Path(csv_dir) / "dim_customers.csv")
    }
    products = {
        r["product_id"]
        for r in _read_csv_rows(Path(csv_dir) / "dim_products.csv")
    }
    return {
        "orphan_customers": sum(r["customer_id"] not in customers for r in sales),
        "orphan_products": sum(r["product_id"] not in products for r in sales),
    }


def live_table_sql(dataset_path: str, table: str, columns: list[str]) -> str:
    cells = ", '\\t', ".join(
        f"IFNULL(CAST({c} AS STRING), '')" for c in columns
    )
    key = KEYS[table]
    return (
        "SELECT COUNT(*) AS row_count, "
        f"COUNT(DISTINCT {key}) AS distinct_keys, "
        f"TO_HEX(SHA256(STRING_AGG(CONCAT({cells}), '\\n' ORDER BY {key}))) "
        f"AS row_sha256 FROM `{dataset_path}.{table}`"
    )


def live_fk_sql(dataset_path: str) -> str:
    return (
        "SELECT COUNTIF(c.customer_id IS NULL) AS orphan_customers, "
        "COUNTIF(p.product_id IS NULL) AS orphan_products "
        f"FROM `{dataset_path}.fact_sales` f "
        f"LEFT JOIN `{dataset_path}.dim_customers` c USING (customer_id) "
        f"LEFT JOIN `{dataset_path}.dim_products` p USING (product_id)"
    )


def _run_one(execution_pipeline: Any, sql: str, fields: dict[str, Any]) -> dict:
    try:
        outcome = execution_pipeline.execute(sql)
    except Exception as error:  # noqa: BLE001 - recorded, not raised
        return {"error": f"{type(error).__name__}: {error}", "stage": "exception"}
    if outcome.get("status") != "success":
        return {"error": outcome.get("message"), "stage": outcome.get("stage")}
    rows = outcome.get("rows") or []
    if not rows:
        return {"error": "no rows returned", "stage": "result"}
    try:
        result = {name: cast(rows[0][name]) for name, cast in fields.items()}
    except (KeyError, ValueError, TypeError) as error:
        return {
            "error": f"unreadable result: {type(error).__name__}: {error}",
            "stage": "result",
        }
    result["evidence"] = {
        "run_id": outcome.get("run_id"),
        "job_id": outcome.get("job_id"),
        "executed_sql": outcome.get("executed_sql"),
    }
    return result


def _comparable(side: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in side.items() if k != "evidence"}


def run_dataset_check(
    execution_pipeline: Any, dataset_path: str, csv_dir: Path
) -> dict[str, Any]:
    csv_dir = Path(csv_dir)
    report: dict[str, Any] = {}

    for table in KEYS:
        columns = _header(csv_dir / f"{table}.csv")
        expected = csv_table_summary(csv_dir, table)
        live = _run_one(
            execution_pipeline,
            live_table_sql(dataset_path, table, columns),
            {"row_count": int, "distinct_keys": int, "row_sha256": str},
        )
        report[table] = {
            "csv": expected,
            "bigquery": live,
            "match": "error" not in live and _comparable(live) == expected,
        }

    csv_fk = csv_orphans(csv_dir)
    live_fk = _run_one(
        execution_pipeline,
        live_fk_sql(dataset_path),
        {"orphan_customers": int, "orphan_products": int},
    )
    report["foreign_keys"] = {
        "csv": csv_fk,
        "bigquery": live_fk,
        "match": "error" not in live_fk
        and _comparable(live_fk) == csv_fk
        and not any(csv_fk.values()),
    }
    report["all_match"] = all(
        report[name]["match"] for name in [*KEYS, "foreign_keys"]
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dataset_check",
        description="Row-level CSV vs BigQuery comparison.",
    )
    parser.add_argument("--out", required=True, metavar="OUT_JSON")
    parser.add_argument("--csv-dir", default="Datasets", metavar="DIR")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from evaluation.report import redact_project_id
    from src import config
    from src.bigquery_service import BigQueryService
    from src.sql_execution_pipeline import SQLExecutionPipeline

    service = BigQueryService()
    pipeline = SQLExecutionPipeline(bigquery_service=service)
    result = run_dataset_check(
        pipeline, service.dataset_path, Path(args.csv_dir)
    )

    result = redact_project_id(result, config.PROJECT_ID)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )

    for name in [*KEYS, "foreign_keys"]:
        entry = result[name]
        print(f"{name}: {'MATCH' if entry['match'] else 'MISMATCH'}")
    print(f"all_match: {result['all_match']}")
    return 0 if result["all_match"] else 1


if __name__ == "__main__":
    sys.exit(main())
