"""Shared fixtures and hermetic-test guards.

Two autouse fixtures keep the suite honest: one makes constructing a live
client fail loudly, the other redirects the log and run-record files into a
temporary directory so tests never append to the project's real logs/.
"""

import logging

import pytest

from src import config
from src.llm.scripted import ScriptedLLMClient
from src.schema_config import RELATIONSHIPS
from src.schema_provider import SchemaProvider
from src.sql_execution_pipeline import SQLExecutionPipeline
from tests.fakes.bigquery import FakeBigQueryService

LIVE_MARKERS = ("live_bq", "live_llm")


def _is_live(request: pytest.FixtureRequest) -> bool:
    """Return whether the requesting test is marked as needing a live service."""

    return any(
        request.node.get_closest_marker(marker) is not None
        for marker in LIVE_MARKERS
    )


@pytest.fixture(autouse=True)
def block_live_clients(request, monkeypatch):
    """Make any accidental live client construction fail immediately.

    Without this, a test that forgets to inject a fake silently reaches for
    application default credentials and either hangs or, worse, succeeds and
    bills real bytes.
    """

    if _is_live(request):
        return

    def _refuse(*args, **kwargs):
        raise RuntimeError(
            "A live client was constructed inside a hermetic test. Inject a "
            "fake, or mark the test with @pytest.mark.live_bq / live_llm."
        )

    from google.cloud import bigquery
    import ollama

    monkeypatch.setattr(bigquery, "Client", _refuse)
    monkeypatch.setattr(ollama, "Client", _refuse)


@pytest.fixture(autouse=True)
def isolated_logs(request, tmp_path, monkeypatch):
    """Redirect application logs and run records into tmp_path."""

    if _is_live(request):
        return

    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "LOG_DIRECTORY", str(log_dir))
    monkeypatch.setattr(
        config, "APPLICATION_LOG_FILE", str(log_dir / "agent_run.log")
    )
    monkeypatch.setattr(config, "RUN_LOG_FILE", str(log_dir / "runs.jsonl"))

    # configure_application_logger returns early when handlers already exist,
    # so a handler pointing at a previous test's tmp_path would leak forward.
    logger = logging.getLogger("word_to_insights")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    yield

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


@pytest.fixture
def run_log_path(tmp_path):
    """Return the path isolated_logs redirects run records to."""

    return tmp_path / "logs" / "runs.jsonl"


@pytest.fixture
def fake_bq():
    """Return a fake BigQuery reader serving canned data."""

    return FakeBigQueryService()


@pytest.fixture
def make_scripted_llm():
    """Return a factory building a ScriptedLLMClient from a response list."""

    def _make(responses=None, **kwargs):
        return ScriptedLLMClient(responses or [], **kwargs)

    return _make


@pytest.fixture
def schema_provider(fake_bq):
    """Return a SchemaProvider backed by the fake reader."""

    return SchemaProvider(
        bigquery_service=fake_bq,
        relationships=RELATIONSHIPS,
    )


@pytest.fixture
def execution_pipeline(fake_bq):
    """Return an SQLExecutionPipeline backed by the fake reader."""

    return SQLExecutionPipeline(bigquery_service=fake_bq)
