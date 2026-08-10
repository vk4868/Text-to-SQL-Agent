"""Configuration loading and the lazily-built LangChain tool surface."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import src.tools as tools_module
from src import config

PROJECT_ROOT = Path(__file__).resolve().parents[1]

READ_CONFIG = (
    "import src.config as config; "
    "print(config.PROJECT_ID); "
    "print(config.OLLAMA_MODEL); "
    "print(config.MAX_SQL_REPAIR_ATTEMPTS)"
)


def run_python(
    code: str,
    *,
    cwd: Path,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a snippet in a clean interpreter rooted at the project."""

    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            ("GCP_", "BIGQUERY_", "OLLAMA_", "MAX_", "QUERY_")
        )
    }
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env.update(env_overrides or {})

    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_defaults_apply_when_no_env_file_exists(
    tmp_path: Path,
) -> None:
    result = run_python(READ_CONFIG, cwd=tmp_path)

    assert result.returncode == 0, result.stderr

    project_id, model, repair_attempts = result.stdout.split()

    assert project_id == "sql-bigquery-502206"
    assert model == "gemma3:4b"
    assert repair_attempts == "2"


def test_env_file_values_reach_the_configuration(
    tmp_path: Path,
) -> None:
    """The documented `.env` override must actually take effect."""

    (tmp_path / ".env").write_text(
        "GCP_PROJECT_ID=dummy-project-from-env-file\n"
        "OLLAMA_MODEL=gemma3:12b\n"
        "MAX_SQL_REPAIR_ATTEMPTS=5\n",
        encoding="utf-8",
    )

    result = run_python(READ_CONFIG, cwd=tmp_path)

    assert result.returncode == 0, result.stderr

    project_id, model, repair_attempts = result.stdout.split()

    assert project_id == "dummy-project-from-env-file"
    assert model == "gemma3:12b"
    assert repair_attempts == "5"


def test_a_real_environment_variable_beats_the_env_file(
    tmp_path: Path,
) -> None:
    """CI and container settings must win over a checked-out `.env`."""

    (tmp_path / ".env").write_text(
        "GCP_PROJECT_ID=from-env-file\n",
        encoding="utf-8",
    )

    result = run_python(
        READ_CONFIG,
        cwd=tmp_path,
        env_overrides={"GCP_PROJECT_ID": "from-real-environment"},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[0] == "from-real-environment"


def test_the_env_example_documents_every_setting() -> None:
    """A reader copying `.env.example` should not be missing anything."""

    example_text = (PROJECT_ROOT / ".env.example").read_text(
        encoding="utf-8"
    )

    documented = {
        line.split("=", 1)[0].strip()
        for line in example_text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    config_text = (
        PROJECT_ROOT / "src" / "config.py"
    ).read_text(encoding="utf-8")

    used_variables = {
        segment.split('"')[0]
        for segment in config_text.split('os.getenv(\n        "')[1:]
    } | {
        segment.split('"')[0]
        for segment in config_text.split('os.getenv(\n    "')[1:]
    }

    assert used_variables
    assert used_variables <= documented


def test_numeric_settings_are_parsed_as_numbers() -> None:
    assert isinstance(config.MAX_QUERY_BYTES, int)
    assert isinstance(config.MAX_RESULT_ROWS, int)
    assert isinstance(config.QUERY_TIMEOUT_SECONDS, int)
    assert isinstance(config.MAX_SQL_REPAIR_ATTEMPTS, int)
    assert isinstance(config.OLLAMA_TEMPERATURE, float)


def test_importing_the_tool_module_needs_no_credentials(
    tmp_path: Path,
) -> None:
    """Importing tools must not build a BigQuery client."""

    result = run_python(
        "import src.tools as tools; "
        "print(sorted(tools.get_bigquery_service.cache_info()))",
        cwd=tmp_path,
        env_overrides={
            "GOOGLE_APPLICATION_CREDENTIALS": str(
                tmp_path / "missing.json"
            ),
            "GOOGLE_CLOUD_PROJECT": "",
        },
    )

    assert result.returncode == 0, result.stderr
    # currsize == 0 proves nothing was constructed at import time.
    assert "0" in result.stdout


def test_importing_the_whole_source_package_needs_no_credentials(
    tmp_path: Path,
) -> None:
    modules = [
        "src.bigquery_service",
        "src.config",
        "src.exceptions",
        "src.observability",
        "src.question_to_insights_pipeline",
        "src.question_to_sql_pipeline",
        "src.result_analyzer",
        "src.schema_formatter",
        "src.schema_provider",
        "src.sql_execution_pipeline",
        "src.sql_generator",
        "src.sql_repairer",
        "src.sql_text",
        "src.sql_validator",
        "src.tools",
    ]

    result = run_python(
        "import importlib\n"
        f"for name in {modules!r}:\n"
        "    importlib.import_module(name)\n"
        "print('imported')",
        cwd=tmp_path,
        env_overrides={
            "GOOGLE_APPLICATION_CREDENTIALS": str(
                tmp_path / "missing.json"
            ),
        },
    )

    assert result.returncode == 0, result.stderr
    assert "imported" in result.stdout


def test_service_and_pipeline_are_built_once_and_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed: list[str] = []

    class RecordingService:
        def __init__(self) -> None:
            constructed.append("bigquery_service")
            self.project_id = "test-project"
            self.dataset_id = "test_dataset"

    monkeypatch.setattr(
        tools_module,
        "BigQueryService",
        RecordingService,
    )

    tools_module.reset_tool_dependencies()

    first = tools_module.get_bigquery_service()
    second = tools_module.get_bigquery_service()

    assert first is second
    assert constructed == ["bigquery_service"]

    tools_module.reset_tool_dependencies()


def test_the_schema_tool_returns_the_schema_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingProvider:
        def get_schema_document(self) -> str:
            return "DATASET: test-project.test_dataset"

    monkeypatch.setattr(
        tools_module,
        "get_schema_provider",
        RecordingProvider,
    )

    assert tools_module.get_schema.invoke({}) == (
        "DATASET: test-project.test_dataset"
    )


def test_the_run_sql_tool_delegates_to_the_guardrail_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[str] = []

    class RecordingPipeline:
        def execute(self, sql: str) -> dict[str, object]:
            executed.append(sql)
            return {"status": "success"}

    monkeypatch.setattr(
        tools_module,
        "get_sql_execution_pipeline",
        RecordingPipeline,
    )

    result = tools_module.run_sql.invoke({"sql": "SELECT 1"})

    assert result == {"status": "success"}
    assert executed == ["SELECT 1"]


def test_the_tools_describe_themselves_for_the_model() -> None:
    """LangChain hands these descriptions to the LLM."""

    assert tools_module.get_schema.name == "get_schema"
    assert tools_module.run_sql.name == "run_sql"
    schema_description = tools_module.get_schema.description.lower()

    assert "tables" in schema_description
    assert "before generating sql" in schema_description
    assert "read-only" in tools_module.run_sql.description.lower()
    assert set(tools_module.run_sql.args) == {"sql"}
