"""Central configuration for the BigQuery SQL agent.

Every setting is read from the environment with a working default, so a
fresh clone runs without a `.env` file. Values in `.env` (see
`.env.example`) override the defaults; real environment variables
override `.env`.
"""

import os

from dotenv import load_dotenv

# Load `.env` from the project root before any setting is read.
# `override=False` keeps real environment variables authoritative,
# which matters in CI and container deployments.
load_dotenv(override=False)

PROJECT_ID = os.getenv(
    "GCP_PROJECT_ID",
    "sql-bigquery-502206",
)

DATASET_ID = os.getenv(
    "BIGQUERY_DATASET_ID",
    "business_insights",
)

BIGQUERY_LOCATION = os.getenv(
    "BIGQUERY_LOCATION",
    "australia-southeast1",
)

MAX_QUERY_BYTES = int(
    os.getenv(
        "MAX_QUERY_BYTES",
        "100000000"
    )
)

MAX_RESULT_ROWS = int(
    os.getenv(
        "MAX_RESULT_ROWS",
        "100"
    )
)

QUERY_TIMEOUT_SECONDS = int(
    os.getenv(
        "QUERY_TIMEOUT_SECONDS",
        "30"
    )
)

LOG_DIRECTORY = os.getenv(
    "LOG_DIRECTORY",
    "logs",
)

APPLICATION_LOG_FILE = os.getenv(
    "APPLICATION_LOG_FILE",
    f"{LOG_DIRECTORY}/agent_run.log",
)

RUN_LOG_FILE = os.getenv(
    "RUN_LOG_FILE",
    f"{LOG_DIRECTORY}/runs.jsonl",
)

OLLAMA_HOST = os.getenv(
    "OLLAMA_HOST",
    "http://localhost:11434",
)

OLLAMA_MODEL = os.getenv(
    "OLLAMA_MODEL",
    "gemma3:4b",
)

OLLAMA_TIMEOUT_SECONDS = float(
    os.getenv(
        "OLLAMA_TIMEOUT_SECONDS",
        "120",
    )
)

OLLAMA_TEMPERATURE = float(
    os.getenv(
        "OLLAMA_TEMPERATURE",
        "0.1",
    )
)

MAX_ANALYSIS_ROWS = int(
    os.getenv(
        "MAX_ANALYSIS_ROWS",
        "50",
    )
)

MAX_SQL_REPAIR_ATTEMPTS = int(
    os.getenv(
        "MAX_SQL_REPAIR_ATTEMPTS",
        "2"
    )
)
