from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import (
    APPLICATION_LOG_FILE,
    RUN_LOG_FILE,
)

def utc_timestamp() -> str:
    """Return the current UTC time in ISO 8601 format."""

    return datetime.now(timezone.utc).isoformat()

def configure_application_logger(
    name: str = "word_to_insights",
    log_file: str = APPLICATION_LOG_FILE,
) -> logging.Logger:
    """ Create a logger that writes to the console and a file"""

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        return logger
    
    log_path = Path(log_file)
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

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger

def write_jsonl_record(
    record: dict[str,Any],
    file_path: str = RUN_LOG_FILE
) -> None:
    """Append one structured record to a JSONL file"""

    output_path = Path(file_path)

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


    