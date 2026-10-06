"""Provenance manifest for an evaluation run.

A pass rate is only comparable across runs when you can show what was held
constant: the code, the data, the model weights, the caps and the cases. These
helpers capture that as plain data. Everything here is a function with no
import-time I/O; configuration is read when called.

Warehouse reads (``live_aggregates``) go through
``SQLExecutionPipeline.execute()`` like every other read in this project.
"""

import csv
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import urllib.request
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from evaluation.cases import (
    PROJECT_PLACEHOLDER,
    load_attack_cases,
    load_golden_cases,
)

MANIFEST_SCHEMA_VERSION = 1

_CENT = Decimal("0.01")

# (table, figure) -> kind. Order here is the order of the reconciliation.
FIGURES: dict[str, dict[str, str]] = {
    "fact_sales": {
        "row_count": "int",
        "distinct_sale_id": "int",
        "sum_net_revenue": "money",
        "sum_profit_amount": "money",
        "sum_quantity": "int",
        "min_sale_date": "date",
        "max_sale_date": "date",
    },
    "dim_products": {
        "row_count": "int",
        "sum_list_price": "money",
        "sum_unit_cost": "money",
    },
    "dim_customers": {
        "row_count": "int",
        "member_count": "int",
    },
}

# Every STRING and DATE column of the bundled dataset (numeric columns are
# covered by FIGURES). The numeric reconciliation alone once missed five
# string columns that differed between the CSVs and BigQuery.
CATEGORICAL_COLUMNS: dict[str, list[str]] = {
    "fact_sales": [
        "sale_id",
        "sale_date",
        "branch_id",
        "branch_city",
        "branch_state",
        "branch_region",
        "customer_id",
        "product_id",
        "payment_method",
        "sales_channel",
        "promotion_type",
    ],
    "dim_products": [
        "product_id",
        "product_name",
        "category",
        "subcategory",
        "brand",
        "launch_date",
    ],
    "dim_customers": [
        "customer_id",
        "customer_name",
        "membership_status",
        "customer_segment",
        "gender",
        "age_group",
        "signup_date",
        "home_city",
        "home_state",
        "acquisition_channel",
    ],
}


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


def _git(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return completed.stdout


def git_identity(repo_root: Path) -> dict[str, Any]:
    """Return head, branch, dirty files and a hash of the uncommitted diff."""

    try:
        head = _git(repo_root, "rev-parse", "HEAD").strip()
        branch = _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD").strip()
        porcelain = _git(repo_root, "status", "--porcelain")
        diff = _git(repo_root, "diff", "HEAD")
        untracked_listing = _git(
            repo_root, "ls-files", "--others", "--exclude-standard"
        )
    except (subprocess.SubprocessError, OSError) as error:
        return {"error": f"{type(error).__name__}: {error}"}

    diff_sha256 = hashlib.sha256(diff.encode("utf-8")).hexdigest()

    untracked: dict[str, str] = {}
    for name in sorted(untracked_listing.splitlines()):
        if not name.strip():
            continue
        path = Path(repo_root) / name
        try:
            if path.is_dir():
                continue
            untracked[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue

    tree = hashlib.sha256()
    tree.update(head.encode("utf-8"))
    tree.update(diff_sha256.encode("utf-8"))
    for name, digest in sorted(untracked.items()):
        tree.update(f"{name}:{digest}".encode("utf-8"))

    return {
        "head": head,
        "branch": branch,
        "dirty_files": [
            line for line in porcelain.splitlines() if line.strip()
        ],
        "diff_sha256": diff_sha256,
        "untracked_sha256": untracked,
        "tree_sha256": tree.hexdigest(),
    }


# ---------------------------------------------------------------------------
# dataset: CSV side
# ---------------------------------------------------------------------------


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def csv_fingerprints(csv_dir: Path) -> dict[str, dict[str, Any]]:
    """Return sha256 and data-row count for every CSV in ``csv_dir``."""

    fingerprints: dict[str, dict[str, Any]] = {}

    for path in sorted(Path(csv_dir).glob("*.csv")):
        fingerprints[path.name] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "rows": len(_read_csv_rows(path)),
        }

    return fingerprints


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    return Decimal(str(value).strip())


def _money(value: Any) -> str:
    return str(_to_decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP))


def _integer(value: Any) -> int:
    return int(_to_decimal(value))


def _iso_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()[:10]


def _normalise(kind: str, value: Any) -> Any:
    if kind == "money":
        return _money(value)
    if kind == "int":
        return _integer(value)
    return _iso_date(value)


def _decimal_sum(rows: list[dict[str, str]], column: str) -> Decimal:
    return sum((Decimal(row[column]) for row in rows), Decimal("0"))


def csv_aggregates(csv_dir: Path) -> dict[str, dict[str, Any]]:
    """Compute the reconciliation figures from the CSVs, using Decimal."""

    csv_dir = Path(csv_dir)
    sales = _read_csv_rows(csv_dir / "fact_sales.csv")
    products = _read_csv_rows(csv_dir / "dim_products.csv")
    customers = _read_csv_rows(csv_dir / "dim_customers.csv")

    dates = [row["sale_date"] for row in sales]

    return {
        "fact_sales": {
            "row_count": len(sales),
            "distinct_sale_id": len({row["sale_id"] for row in sales}),
            "sum_net_revenue": _money(_decimal_sum(sales, "net_revenue")),
            "sum_profit_amount": _money(
                _decimal_sum(sales, "profit_amount")
            ),
            "sum_quantity": int(_decimal_sum(sales, "quantity")),
            "min_sale_date": min(dates) if dates else None,
            "max_sale_date": max(dates) if dates else None,
        },
        "dim_products": {
            "row_count": len(products),
            "sum_list_price": _money(_decimal_sum(products, "list_price")),
            "sum_unit_cost": _money(_decimal_sum(products, "unit_cost")),
        },
        "dim_customers": {
            "row_count": len(customers),
            "member_count": sum(
                1
                for row in customers
                if row.get("membership_status") == "Member"
            ),
        },
    }


def _values_fingerprint(values: set[str]) -> dict[str, Any]:
    """Distinct count and sha256 of the sorted values joined by ``|``."""

    joined = "|".join(sorted(values))
    return {
        "distinct": len(values),
        "sha256": hashlib.sha256(joined.encode("utf-8")).hexdigest(),
    }


def csv_column_fingerprints(
    csv_dir: Path,
    columns: dict[str, list[str]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Per-column distinct count and value hash for the categorical columns.

    Empty values are ignored (BigQuery's ``STRING_AGG`` ignores NULLs). A
    table whose file or columns are missing is recorded as ``{"error": ...}``.
    """

    wanted = CATEGORICAL_COLUMNS if columns is None else columns
    result: dict[str, dict[str, Any]] = {}

    for table, names in wanted.items():
        try:
            rows = _read_csv_rows(Path(csv_dir) / f"{table}.csv")
            table_prints: dict[str, Any] = {}
            for name in names:
                values = {
                    row[name]
                    for row in rows
                    if row[name] is not None and row[name] != ""
                }
                table_prints[name] = _values_fingerprint(values)
            result[table] = table_prints
        except (OSError, KeyError) as error:
            result[table] = {
                "error": f"{type(error).__name__}: {error}",
            }

    return result


# ---------------------------------------------------------------------------
# dataset: BigQuery side
# ---------------------------------------------------------------------------


def live_table_metadata(bigquery_service: Any) -> list[dict[str, Any]]:
    """Summarise the live tables from ``get_dataset_structure()``."""

    tables: list[dict[str, Any]] = []

    for entry in bigquery_service.get_dataset_structure():
        meta = entry.get("metadata") or {}
        tables.append(
            {
                "table_name": meta.get("table_name"),
                "row_count": meta.get("row_count"),
                "size_bytes": meta.get("size_bytes"),
                "partition_field": meta.get("partition_field"),
                "partition_type": meta.get("partition_type"),
                "clustering_fields": list(
                    meta.get("clustering_fields") or []
                ),
                "columns": [
                    {"name": col.get("name"), "type": col.get("type")}
                    for col in entry.get("columns") or []
                ],
                "location": getattr(bigquery_service, "location", None),
                "dataset_path": getattr(
                    bigquery_service, "dataset_path", None
                ),
            }
        )

    return tables


def live_aggregate_sql(dataset_path: str) -> dict[str, str]:
    """The three statements ``live_aggregates`` runs, one per table."""

    return {
        "fact_sales": (
            "SELECT COUNT(*) AS row_count, "
            "COUNT(DISTINCT sale_id) AS distinct_sale_id, "
            "SUM(net_revenue) AS sum_net_revenue, "
            "SUM(profit_amount) AS sum_profit_amount, "
            "SUM(quantity) AS sum_quantity, "
            "MIN(sale_date) AS min_sale_date, "
            "MAX(sale_date) AS max_sale_date "
            f"FROM `{dataset_path}.fact_sales`"
        ),
        "dim_products": (
            "SELECT COUNT(*) AS row_count, "
            "SUM(list_price) AS sum_list_price, "
            "SUM(unit_cost) AS sum_unit_cost "
            f"FROM `{dataset_path}.dim_products`"
        ),
        "dim_customers": (
            "SELECT COUNT(*) AS row_count, "
            "COUNTIF(membership_status = 'Member') AS member_count "
            f"FROM `{dataset_path}.dim_customers`"
        ),
    }


def live_aggregates(
    execution_pipeline: Any, dataset_path: str
) -> dict[str, dict[str, Any]]:
    """Compute the reconciliation figures in BigQuery, via the pipeline."""

    results: dict[str, dict[str, Any]] = {}

    for table, sql in live_aggregate_sql(dataset_path).items():
        try:
            outcome = execution_pipeline.execute(sql)
        except Exception as error:  # noqa: BLE001 - recorded, not raised
            results[table] = {
                "error": f"{type(error).__name__}: {error}",
                "stage": "exception",
            }
            continue

        if outcome.get("status") != "success":
            results[table] = {
                "error": outcome.get("message"),
                "stage": outcome.get("stage"),
            }
            continue

        rows = outcome.get("rows") or []
        if not rows:
            results[table] = {"error": "no rows returned", "stage": "result"}
            continue

        row = rows[0]
        figures: dict[str, Any] = {}
        try:
            for figure, kind in FIGURES[table].items():
                figures[figure] = _normalise(kind, row[figure])
        except (KeyError, InvalidOperation, ValueError, TypeError) as error:
            results[table] = {
                "error": f"unreadable result: {type(error).__name__}: "
                f"{error}",
                "stage": "result",
            }
            continue

        figures["evidence"] = {
            "run_id": outcome.get("run_id"),
            "job_id": outcome.get("job_id"),
            "total_bytes_billed": outcome.get("total_bytes_billed"),
            "cache_hit": outcome.get("cache_hit"),
            "executed_sql": outcome.get("executed_sql"),
        }
        results[table] = figures

    return results


def live_column_fingerprint_sql(
    dataset_path: str,
    columns: dict[str, list[str]] | None = None,
) -> dict[str, str]:
    """One statement per table fingerprinting every categorical column."""

    wanted = CATEGORICAL_COLUMNS if columns is None else columns
    statements: dict[str, str] = {}

    for table, names in wanted.items():
        parts: list[str] = []
        for name in names:
            parts.append(f"COUNT(DISTINCT {name}) AS {name}__distinct")
            parts.append(
                "TO_HEX(SHA256(STRING_AGG(DISTINCT CAST("
                f"{name} AS STRING), '|' ORDER BY CAST({name} AS STRING)"
                f"))) AS {name}__sha256"
            )
        statements[table] = (
            f"SELECT {', '.join(parts)} FROM `{dataset_path}.{table}`"
        )

    return statements


def live_column_fingerprints(
    execution_pipeline: Any,
    dataset_path: str,
    columns: dict[str, list[str]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Fingerprint the categorical columns in BigQuery, one query per table.

    Returns ``{table: {column: {"distinct", "sha256"}, "evidence": {...}}}``;
    a failure is ``{"error", "stage"}`` for that table and is never raised.
    """

    wanted = CATEGORICAL_COLUMNS if columns is None else columns
    results: dict[str, dict[str, Any]] = {}

    for table, sql in live_column_fingerprint_sql(
        dataset_path, wanted
    ).items():
        try:
            outcome = execution_pipeline.execute(sql)
        except Exception as error:  # noqa: BLE001 - recorded, not raised
            results[table] = {
                "error": f"{type(error).__name__}: {error}",
                "stage": "exception",
            }
            continue

        if outcome.get("status") != "success":
            results[table] = {
                "error": outcome.get("message"),
                "stage": outcome.get("stage"),
            }
            continue

        rows = outcome.get("rows") or []
        if not rows:
            results[table] = {"error": "no rows returned", "stage": "result"}
            continue

        row = rows[0]
        prints: dict[str, Any] = {}
        try:
            for name in wanted[table]:
                digest = row[f"{name}__sha256"]
                prints[name] = {
                    "distinct": int(row[f"{name}__distinct"]),
                    "sha256": (
                        None if digest is None else str(digest).lower()
                    ),
                }
        except (KeyError, ValueError, TypeError) as error:
            results[table] = {
                "error": f"unreadable result: {type(error).__name__}: "
                f"{error}",
                "stage": "result",
            }
            continue

        prints["evidence"] = {
            "run_id": outcome.get("run_id"),
            "job_id": outcome.get("job_id"),
            "total_bytes_billed": outcome.get("total_bytes_billed"),
            "cache_hit": outcome.get("cache_hit"),
            "executed_sql": outcome.get("executed_sql"),
        }
        results[table] = prints

    return results


def _comparable(table: str, figure: str, value: Any) -> Any:
    """Normalise ``value`` with the FIGURES kind; unknown figures use str."""

    kind = FIGURES.get(table, {}).get(figure)
    if kind is None:
        return str(value)
    return _normalise(kind, value)


def reconcile(
    csv: dict[str, dict[str, Any]], live: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """One row per (table, figure), compared as typed normalised values.

    A missing figure on either side never matches, and a value that cannot be
    normalised is recorded as a mismatch with a ``note`` rather than raised.
    """

    rows: list[dict[str, Any]] = []

    for table, csv_figures in csv.items():
        live_figures = live.get(table) or {}
        for figure, csv_value in csv_figures.items():
            if figure == "evidence":
                continue
            live_value = live_figures.get(figure)
            row: dict[str, Any] = {
                "table": table,
                "figure": figure,
                "csv": csv_value,
                "bigquery": live_value,
                "match": False,
            }
            if csv_value is not None and live_value is not None:
                try:
                    row["match"] = _comparable(
                        table, figure, csv_value
                    ) == _comparable(table, figure, live_value)
                except Exception as error:  # noqa: BLE001
                    row["match"] = False
                    row["note"] = f"{type(error).__name__}: {error}"
            rows.append(row)

    return rows


def reconcile_columns(
    csv_columns: dict[str, dict[str, Any]],
    live_columns: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rows ``distinct:<column>`` and ``sha256:<column>`` per table/column.

    A column with no live figure (missing, or its table errored) never
    matches. Tables whose CSV side errored contribute no rows; the caller
    treats that as a failed dataset section.
    """

    rows: list[dict[str, Any]] = []

    for table, csv_table in csv_columns.items():
        if "error" in csv_table:
            continue
        live_table = live_columns.get(table) or {}
        for column, csv_print in csv_table.items():
            if column == "evidence" or not isinstance(csv_print, dict):
                continue
            live_print = live_table.get(column)
            if not isinstance(live_print, dict):
                live_print = {}
            for kind in ("distinct", "sha256"):
                csv_value = csv_print.get(kind)
                live_value = live_print.get(kind)
                rows.append(
                    {
                        "table": table,
                        "figure": f"{kind}:{column}",
                        "csv": csv_value,
                        "bigquery": live_value,
                        "match": (
                            csv_value is not None
                            and live_value is not None
                            and str(csv_value) == str(live_value)
                        ),
                    }
                )

    return rows


# ---------------------------------------------------------------------------
# model, config, environment, cases
# ---------------------------------------------------------------------------


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _normalise_tag(name: Any) -> str | None:
    """Treat ``gemma`` and ``gemma:latest`` as the same model tag."""

    if not name:
        return None
    text = str(name).strip()
    return text if ":" in text else f"{text}:latest"


def _default_ollama_provider(
    model_name: str, host: str, timeout: float
) -> dict[str, Any]:
    """Ask a running Ollama server who the model is. Built lazily."""

    import ollama

    gathered: dict[str, Any] = {"tag": model_name}

    try:
        client = ollama.Client(host=host, timeout=timeout)

        shown = client.show(model_name)
        details = _field(shown, "details")
        gathered["family"] = _field(details, "family")
        gathered["parameter_size"] = _field(details, "parameter_size")
        gathered["quantization_level"] = _field(
            details, "quantization_level"
        )
        gathered["modified_at"] = _field(shown, "modified_at")

        wanted = _normalise_tag(model_name)
        for item in _field(client.list(), "models", []) or []:
            names = {
                _normalise_tag(_field(item, "model")),
                _normalise_tag(_field(item, "name")),
            }
            if wanted in names:
                gathered["digest"] = _field(item, "digest")
                break
        else:
            gathered["digest_missing"] = True

        gathered["loaded_models"] = [
            {
                "model": _field(item, "model"),
                "digest": _field(item, "digest"),
                "size": _field(item, "size"),
                "size_vram": _field(item, "size_vram"),
                "expires_at": _field(item, "expires_at"),
            }
            for item in _field(client.ps(), "models", []) or []
        ]

        with urllib.request.urlopen(
            f"{host.rstrip('/')}/api/version", timeout=timeout
        ) as response:
            gathered["server_version"] = json.loads(
                response.read().decode("utf-8")
            ).get("version")
    except Exception as error:  # noqa: BLE001 - keep what was gathered
        gathered["error"] = f"{type(error).__name__}: {error}"

    return gathered


def ollama_model_identity(
    model_name: str,
    host: str,
    timeout_seconds: float,
    *,
    provider: Callable[[str, str, float], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Identify the served model by digest, not just by tag."""

    identity: dict[str, Any] = {
        "tag": model_name,
        "digest": None,
        "family": None,
        "parameter_size": None,
        "quantization_level": None,
        "modified_at": None,
        "server_version": None,
        "loaded_models": [],
    }

    if provider is None:
        provider = _default_ollama_provider

    try:
        identity.update(provider(model_name, host, timeout_seconds))
    except Exception as error:  # noqa: BLE001
        identity["error"] = f"{type(error).__name__}: {error}"

    return identity


def config_snapshot() -> dict[str, Any]:
    """The caps and settings in force, read at call time."""

    from src import config

    return {
        "PROJECT_ID": config.PROJECT_ID,
        "DATASET_ID": config.DATASET_ID,
        "BIGQUERY_LOCATION": config.BIGQUERY_LOCATION,
        "MAX_QUERY_BYTES": config.MAX_QUERY_BYTES,
        "MAX_RESULT_ROWS": config.MAX_RESULT_ROWS,
        "QUERY_TIMEOUT_SECONDS": config.QUERY_TIMEOUT_SECONDS,
        "MAX_SQL_REPAIR_ATTEMPTS": config.MAX_SQL_REPAIR_ATTEMPTS,
        "MAX_ANALYSIS_ROWS": config.MAX_ANALYSIS_ROWS,
        "SCHEMA_CACHE_TTL_SECONDS": config.SCHEMA_CACHE_TTL_SECONDS,
        "OLLAMA_MODEL": config.OLLAMA_MODEL,
        "OLLAMA_HOST": config.OLLAMA_HOST,
        "OLLAMA_TEMPERATURE": config.OLLAMA_TEMPERATURE,
        "OLLAMA_TIMEOUT_SECONDS": config.OLLAMA_TIMEOUT_SECONDS,
    }


def environment_snapshot() -> dict[str, Any]:
    """Interpreter, OS and hardware, as far as they can be learned."""

    processor = platform.processor()

    if sys.platform == "darwin":
        try:
            brand = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            ).stdout.strip()
            if brand:
                processor = brand
        except (subprocess.SubprocessError, OSError):
            pass

    try:
        memory_bytes: int | None = int(
            os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        )
    except (ValueError, OSError, AttributeError):
        memory_bytes = None

    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": processor,
        "memory_bytes": memory_bytes,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


def case_fingerprints(paths: list[Path]) -> dict[str, dict[str, Any]]:
    """Hash each case file and list its ids, independent of the project."""

    fingerprints: dict[str, dict[str, Any]] = {}

    for path in paths:
        path = Path(path)
        # Loaded with the placeholder so ids do not depend on the live
        # project; the hash is of the file bytes, which hardcode it already.
        loader = (
            load_attack_cases
            if path.name.startswith("attack")
            else load_golden_cases
        )
        cases = loader(path, project_id=PROJECT_PLACEHOLDER)
        fingerprints[path.name] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "case_ids": [case.case_id for case in cases],
        }

    return fingerprints


def file_fingerprints(paths: list[Path]) -> dict[str, dict[str, Any]]:
    """Hash each file's raw bytes: ``{name: {"sha256", "size"}}``."""

    fingerprints: dict[str, dict[str, Any]] = {}

    for path in paths:
        path = Path(path)
        content = path.read_bytes()
        fingerprints[path.name] = {
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }

    return fingerprints


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------


VERSIONED_PACKAGES = [
    "sqlglot",
    "langgraph",
    "langchain",
    "ollama",
    "google-cloud-bigquery",
    "streamlit",
    "pyyaml",
]


def versions_snapshot(repo_root: Path) -> dict[str, Any]:
    """Record-format versions, key package versions and the lockfile hash."""

    from src.run_record import RUN_RECORD_VERSION

    packages: dict[str, str | None] = {}
    for name in VERSIONED_PACKAGES:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None

    lock = Path(repo_root) / "uv.lock"
    lock_hash = (
        hashlib.sha256(lock.read_bytes()).hexdigest()
        if lock.is_file()
        else None
    )

    return {
        "run_record_version": RUN_RECORD_VERSION,
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "registry_version": None,
        "packages": packages,
        "uv_lock_sha256": lock_hash,
    }


def _section(builder: Callable[[], Any]) -> Any:
    """Run one manifest section; a failure stays inside that section."""

    try:
        return builder()
    except Exception as error:  # noqa: BLE001 - isolate, never abort
        return {"error": f"{type(error).__name__}: {error}"}


def _errored(section: Any) -> bool:
    return isinstance(section, dict) and "error" in section


def build_manifest(
    *,
    repo_root: Path,
    bigquery_service: Any,
    execution_pipeline: Any,
    csv_dir: Path,
    case_paths: list[Path],
    model_identity: dict[str, Any],
    trials: int,
    notes: str = "",
    extra_files: list[Path] | None = None,
) -> dict[str, Any]:
    """Assemble the whole provenance manifest, one isolated section at a time."""

    csv_prints = _section(lambda: csv_fingerprints(csv_dir))
    csv_figures = _section(lambda: csv_aggregates(csv_dir))
    live_tables = _section(lambda: live_table_metadata(bigquery_service))
    live_figures = _section(
        lambda: live_aggregates(
            execution_pipeline, str(bigquery_service.dataset_path)
        )
    )

    csv_columns = _section(lambda: csv_column_fingerprints(csv_dir))
    live_columns = _section(
        lambda: live_column_fingerprints(
            execution_pipeline, str(bigquery_service.dataset_path)
        )
    )

    def _table_errors(section: Any) -> bool:
        return isinstance(section, dict) and any(
            _errored(entry) for entry in section.values()
        )

    dataset_failed = any(
        _errored(part)
        for part in (
            csv_prints,
            csv_figures,
            live_tables,
            live_figures,
            csv_columns,
            live_columns,
        )
    ) or any(_table_errors(part) for part in (csv_columns, live_columns))

    reconciliation: Any = []
    if not (_errored(csv_figures) or _errored(live_figures)):
        reconciliation = _section(
            lambda: reconcile(csv_figures, live_figures)
        )
        if _errored(reconciliation):
            dataset_failed = True
            reconciliation = []

    if not (_errored(csv_columns) or _errored(live_columns)):
        column_rows = _section(
            lambda: reconcile_columns(csv_columns, live_columns)
        )
        if _errored(column_rows):
            dataset_failed = True
        else:
            reconciliation = list(reconciliation) + column_rows

    all_match = (
        not dataset_failed
        and bool(reconciliation)
        and all(row["match"] for row in reconciliation)
    )

    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "git": _section(lambda: git_identity(repo_root)),
        "dataset": {
            "csv": csv_prints,
            "live_tables": live_tables,
            "csv_aggregates": csv_figures,
            "live_aggregates": live_figures,
            "csv_columns": csv_columns,
            "live_columns": live_columns,
            "reconciliation": reconciliation,
            "all_match": all_match,
        },
        "model": model_identity,
        "config": _section(config_snapshot),
        "cases": _section(lambda: case_fingerprints(case_paths)),
        "files": _section(lambda: file_fingerprints(extra_files or [])),
        "environment": _section(environment_snapshot),
        "versions": _section(lambda: versions_snapshot(repo_root)),
        "trials": {"count": trials, "notes": notes},
    }
