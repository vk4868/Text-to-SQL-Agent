from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Imported as a module, not as `from src.config import RUN_LOG_FILE`, so the
# values are read at call time rather than captured at import time. Binding
# them as default arguments would make them impossible to redirect in tests.
from src import config

# Marks the handlers this module installed, so repeat calls can tell its own
# handlers apart from ones a host application attached.
_OWNED_FLAG = "_word_to_insights_handler"

def utc_timestamp() -> str:
    """Return the current UTC time in ISO 8601 format."""

    return datetime.now(timezone.utc).isoformat()

def configure_application_logger(
    name: str = "word_to_insights",
    log_file: str | None = None,
) -> logging.Logger:
    """ Create a logger that writes to the console and a file"""

    log_file = log_file or config.APPLICATION_LOG_FILE

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    log_path = Path(log_file).resolve()

    # Idempotency is keyed to the handlers this function installed, not to
    # "does the logger have any handler at all". A host that has already
    # attached its own handler (a test runner's log capture, for instance)
    # would otherwise make this function silently skip file logging.
    installed = [
        handler
        for handler in logger.handlers
        if getattr(handler, _OWNED_FLAG, False)
    ]

    if installed:
        already_targeting = any(
            isinstance(handler, logging.FileHandler)
            and Path(handler.baseFilename) == log_path
            for handler in installed
        )
        if already_targeting:
            return logger

        # The destination changed, so replace our handlers rather than
        # accumulating a new pair on every call.
        for handler in installed:
            logger.removeHandler(handler)
            handler.close()

    log_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s |"
        " %(name)s | %(message)s"
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(
        log_path, encoding = "utf-8",
    )

    file_handler.setFormatter(formatter)

    for handler in (console_handler, file_handler):
        setattr(handler, _OWNED_FLAG, True)
        logger.addHandler(handler)

    return logger

def write_jsonl_record(
    record: dict[str,Any],
    file_path: str | None = None,
) -> None:
    """Append one structured record to a JSONL file"""

    output_path = Path(file_path or config.RUN_LOG_FILE)

    output_path.parent.mkdir(
        parents = True,
        exist_ok = True,
    )

    complete_record = {
        "timestamp_utc":utc_timestamp(),
        **record,
    }

    with output_path.open(
        mode = "a",
        encoding="utf-8",
    ) as file:
        json_line = json.dumps(
            complete_record, ensure_ascii=False,
            default = str,
        )
        file.write(json_line + "\n")


    