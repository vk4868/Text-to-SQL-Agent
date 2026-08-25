"""Gate 0: the foundation every later phase depends on.

Each test here pins one Phase 0 fix. If any of these fail, later phases are
not verifiable — the run log cannot be redirected, config cannot be
overridden, or the suite cannot even be collected.
"""

import importlib
import json

import pytest

from src import config, observability
from src.llm.scripted import (
    ScriptedLLMClient,
    ScriptedLLMExhaustedError,
)
from src.schema_config import RELATIONSHIPS
from src.schema_provider import SchemaProvider
from tests.fakes.bigquery import FakeBigQueryService


IMPORT_PROBE = """
import pkgutil, importlib, sys
from google.cloud import bigquery
import ollama

def refuse(*args, **kwargs):
    raise RuntimeError("live client constructed at import time")

bigquery.Client = refuse
ollama.Client = refuse

import src
failures = []
for module in pkgutil.walk_packages(src.__path__, prefix="src."):
    try:
        importlib.import_module(module.name)
    except Exception as error:
        failures.append(f"{module.name}: {type(error).__name__}: {error}")

print("FAILURES:" + "; ".join(failures))
"""


class TestNoImportTimeIO:
    """Importing any module must not construct a live client (0.4)."""

    def test_every_src_module_imports_without_credentials(self):
        """Run in a subprocess: a fresh interpreter with no live clients.

        Doing this in-process would mean deleting entries from sys.modules and
        re-importing, which leaves the rest of the session holding stale module
        objects and silently breaks later tests.
        """

        import subprocess
        import sys
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]

        completed = subprocess.run(
            [sys.executable, "-c", IMPORT_PROBE],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert completed.returncode == 0, completed.stderr

        failures = completed.stdout.split("FAILURES:", 1)[1].strip()

        assert not failures, "modules performed I/O at import: " + failures

    def test_build_tools_accepts_a_fake_reader(self):
        from src.tools import build_tools

        get_schema, run_sql = build_tools(
            bigquery_service=FakeBigQueryService()
        )

        document = get_schema.invoke({})

        assert "fact_sales" in document
        assert run_sql.name == "run_sql"


class TestConfigLoading:
    """.env and environment variables must reach config (0.2, 0.3)."""

    def test_environment_variable_overrides_default(self, monkeypatch):
        monkeypatch.setenv("MAX_RESULT_ROWS", "7")

        reloaded = importlib.reload(config)
        try:
            assert reloaded.MAX_RESULT_ROWS == 7
        finally:
            monkeypatch.delenv("MAX_RESULT_ROWS", raising=False)
            importlib.reload(config)

    def test_dotenv_is_loaded_before_settings_are_read(self):
        """Parsed from the AST, so comments mentioning getenv don't count."""

        import ast
        import inspect

        tree = ast.parse(inspect.getsource(config))

        load_lines = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "load_dotenv"
        ]
        getenv_lines = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "getenv"
        ]

        assert load_lines, "src/config.py never calls load_dotenv()"
        assert getenv_lines, "expected config to read environment variables"
        assert min(load_lines) < min(getenv_lines), (
            "load_dotenv() must run before any os.getenv call, since every "
            "setting is resolved at import time"
        )

    def test_schema_cache_ttl_setting_exists(self):
        assert config.SCHEMA_CACHE_TTL_SECONDS > 0


class TestRunLogRedirection:
    """Observability must resolve paths at call time, not import time (0.3)."""

    def test_write_jsonl_record_honours_patched_config(
        self, run_log_path
    ):
        observability.write_jsonl_record({"event": "unit_test", "value": 1})

        assert run_log_path.exists(), (
            "run record did not land in the redirected path; RUN_LOG_FILE is "
            "probably still bound as an import-time default argument"
        )

        record = json.loads(run_log_path.read_text().splitlines()[0])

        assert record["event"] == "unit_test"
        assert record["value"] == 1
        assert "timestamp_utc" in record

    def test_explicit_path_still_wins(self, tmp_path):
        target = tmp_path / "explicit.jsonl"

        observability.write_jsonl_record({"event": "explicit"}, str(target))

        assert json.loads(target.read_text())["event"] == "explicit"

    def test_logger_writes_to_redirected_file(self, tmp_path):
        logger = observability.configure_application_logger()
        logger.info("hello from the gate 0 test")

        log_file = tmp_path / "logs" / "agent_run.log"

        assert log_file.exists()
        assert "hello from the gate 0 test" in log_file.read_text()

    def test_file_logging_survives_a_foreign_handler(self, tmp_path):
        """A host's own handler must not suppress our file handler.

        The original guard returned early when the logger had *any* handler,
        so anything that attached one first silently disabled file logging.
        """

        import logging as stdlib_logging

        logger = stdlib_logging.getLogger("word_to_insights")
        foreign = stdlib_logging.NullHandler()
        logger.addHandler(foreign)

        try:
            observability.configure_application_logger()
            logger.info("written despite the foreign handler")

            log_file = tmp_path / "logs" / "agent_run.log"

            assert log_file.exists()
            assert "despite the foreign handler" in log_file.read_text()
        finally:
            logger.removeHandler(foreign)

    def test_repeat_configuration_does_not_duplicate_handlers(self):
        first = observability.configure_application_logger()
        before = len(first.handlers)

        second = observability.configure_application_logger()

        assert second is first
        assert len(second.handlers) == before


class TestSchemaCaching:
    """The schema document must not be re-fetched on every call (0.6)."""

    def test_repeated_calls_fetch_once(self, fake_bq):
        provider = SchemaProvider(
            bigquery_service=fake_bq,
            relationships=RELATIONSHIPS,
        )

        first = provider.get_schema_document()
        second = provider.get_schema_document()

        assert first == second
        assert provider.fetch_count == 1
        assert fake_bq.dataset_structure_calls == 1

    def test_force_refresh_refetches(self, fake_bq):
        provider = SchemaProvider(
            bigquery_service=fake_bq,
            relationships=RELATIONSHIPS,
        )

        provider.get_schema_document()
        provider.get_schema_document(force_refresh=True)

        assert provider.fetch_count == 2

    def test_zero_ttl_disables_caching(self, fake_bq):
        provider = SchemaProvider(
            bigquery_service=fake_bq,
            relationships=RELATIONSHIPS,
            cache_ttl_seconds=0,
        )

        provider.get_schema_document()
        provider.get_schema_document()

        assert provider.fetch_count == 2

    def test_invalidate_cache_forces_a_refetch(self, fake_bq):
        provider = SchemaProvider(
            bigquery_service=fake_bq,
            relationships=RELATIONSHIPS,
        )

        provider.get_schema_document()
        provider.invalidate_cache()
        provider.get_schema_document()

        assert provider.fetch_count == 2

    def test_document_contains_relationships_and_partitioning(
        self, schema_provider
    ):
        document = schema_provider.get_schema_document()

        assert "RELATIONSHIPS:" in document
        assert "PARTITIONED BY: sale_date (DAY)" in document
        assert "net_revenue" in document


class TestScriptedLLMClient:
    """The scripted fake is what makes every later gate hermetic (0.7)."""

    def test_returns_responses_in_order(self):
        client = ScriptedLLMClient(["first", "second"])

        assert client.generate_response("a").text == "first"
        assert client.generate_response("b").text == "second"
        assert client.prompts == ["a", "b"]
        assert client.call_count == 2

    def test_raises_when_exhausted(self):
        client = ScriptedLLMClient(["only one"])
        client.generate_response("a")

        with pytest.raises(ScriptedLLMExhaustedError):
            client.generate_response("b")

    def test_scripted_exception_is_raised(self):
        from src.exceptions import LLMProviderError

        client = ScriptedLLMClient([LLMProviderError("ollama is down")])

        with pytest.raises(LLMProviderError, match="ollama is down"):
            client.generate_response("a")

    def test_token_counts_are_deterministic(self):
        client = ScriptedLLMClient(
            ["SELECT 1"], input_tokens=17, output_tokens=23
        )

        response = client.generate_response("prompt")

        assert response.input_tokens == 17
        assert response.output_tokens == 23
        assert response.total_tokens == 40

    def test_satisfies_the_llm_client_protocol(self):
        from src.llm.base import LLMClient

        client: LLMClient = ScriptedLLMClient(["ok"])

        assert client.generate_response("x").text == "ok"


class TestBugSweep:
    """The Phase 0 bug fixes stay fixed (0.8)."""

    def test_exceptions_module_has_no_private_google_import(self):
        import inspect

        from src import exceptions

        assert "_job_helpers" not in inspect.getsource(exceptions)

    def test_timeout_message_is_readable(self):
        from src.exceptions import QueryExecutionTimeoutError

        error = QueryExecutionTimeoutError(
            job_id="job-1",
            timeout_seconds=30,
            cancel_requested=True,
        )

        message = str(error)

        assert "exceeded the 30-second timeout." in message
        assert "timeout.Cancellation" not in message

    def test_read_only_rejection_message_is_readable(self):
        from src.sql_validator import validate_read_only_sql

        result = validate_read_only_sql("DELETE FROM `p.d.t` WHERE x = 1")

        assert result.is_valid is False
        assert "allowed. Received:" in result.message
