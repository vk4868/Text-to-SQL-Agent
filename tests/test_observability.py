"""Logging and the structured JSONL run record."""

import json
import logging
from pathlib import Path

from src.observability import (
    configure_application_logger,
    utc_timestamp,
    write_jsonl_record,
)


def test_a_record_is_written_as_one_json_line(tmp_path: Path) -> None:
    log_file = tmp_path / "runs.jsonl"

    write_jsonl_record(
        {"event": "sql_execution", "status": "success"},
        file_path=str(log_file),
    )

    lines = log_file.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 1

    record = json.loads(lines[0])

    assert record["event"] == "sql_execution"
    assert record["status"] == "success"


def test_every_record_is_timestamped(tmp_path: Path) -> None:
    log_file = tmp_path / "runs.jsonl"

    write_jsonl_record({"event": "test"}, file_path=str(log_file))

    record = json.loads(log_file.read_text(encoding="utf-8"))

    assert "timestamp_utc" in record
    assert record["timestamp_utc"].endswith("+00:00")


def test_records_are_appended_not_overwritten(tmp_path: Path) -> None:
    log_file = tmp_path / "runs.jsonl"

    for index in range(3):
        write_jsonl_record(
            {"event": "test", "index": index},
            file_path=str(log_file),
        )

    lines = log_file.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 3
    assert [json.loads(line)["index"] for line in lines] == [0, 1, 2]


def test_the_log_directory_is_created_on_demand(
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "nested" / "deeper" / "runs.jsonl"

    write_jsonl_record({"event": "test"}, file_path=str(log_file))

    assert log_file.exists()


def test_unserialisable_values_do_not_break_logging(
    tmp_path: Path,
) -> None:
    """A run record must never take down the run that produced it."""

    log_file = tmp_path / "runs.jsonl"

    write_jsonl_record(
        {"event": "test", "value": object()},
        file_path=str(log_file),
    )

    record = json.loads(log_file.read_text(encoding="utf-8"))

    assert isinstance(record["value"], str)


def test_the_logger_writes_to_console_and_file(
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "agent_run.log"

    logger = configure_application_logger(
        name="tests.observability.file",
        log_file=str(log_file),
    )

    logger.info("hello from the test suite")

    for handler in logger.handlers:
        handler.flush()

    assert log_file.exists()
    assert "hello from the test suite" in log_file.read_text(
        encoding="utf-8"
    )
    assert any(
        isinstance(handler, logging.StreamHandler)
        for handler in logger.handlers
    )


def test_configuring_the_logger_twice_does_not_duplicate_handlers(
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "agent_run.log"

    first = configure_application_logger(
        name="tests.observability.idempotent",
        log_file=str(log_file),
    )
    handler_count = len(first.handlers)

    second = configure_application_logger(
        name="tests.observability.idempotent",
        log_file=str(log_file),
    )

    assert first is second
    assert len(second.handlers) == handler_count


def test_the_timestamp_is_utc_and_iso_formatted() -> None:
    timestamp = utc_timestamp()

    assert "T" in timestamp
    assert timestamp.endswith("+00:00")
