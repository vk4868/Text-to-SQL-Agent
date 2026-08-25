"""When a failed query is worth asking the model to repair.

This policy lived in two places — the graph's routing function and the
imperative pipeline's loop — and the two could drift apart silently. Keeping
it here means "what is repairable" has exactly one definition.
"""

from src import config

#: Failure stages a model can plausibly fix by rewriting the SQL.
#:
#: - validation:   the statement was not a single read-only query
#: - table_access: it referenced a table outside the allowed dataset, or an
#:                 unqualified name
#: - dry_run:      BigQuery rejected it, typically a hallucinated column
#:
#: Everything else is deliberately excluded. A cost_check or result_limit
#: rejection means the query was understood and refused on policy, and an
#: execution or timeout error is an infrastructure problem — in neither case
#: does asking the model to try again help, and retrying a timeout costs real
#: money.
REPAIRABLE_STAGES: frozenset[str] = frozenset(
    {
        "validation",
        "table_access",
        "dry_run",
    }
)


def is_repairable_stage(failure_stage: str) -> bool:
    """Return whether a failure at this stage can be repaired by the model."""

    return failure_stage in REPAIRABLE_STAGES


def should_repair(
    *,
    failure_stage: str,
    repairs_used: int,
    max_repair_attempts: int | None = None,
) -> bool:
    """Return whether another repair attempt should be made."""

    if max_repair_attempts is None:
        max_repair_attempts = config.MAX_SQL_REPAIR_ATTEMPTS

    if not is_repairable_stage(failure_stage):
        return False

    return repairs_used < max_repair_attempts
