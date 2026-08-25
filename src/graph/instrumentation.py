"""Per-node timing for the graph.

Replaces a hand-rolled helper that only one node called and that recorded
nothing on error paths, so a run that failed left no trace of where it spent
its time. Wrapping the node instead means every exit is timed, including the
early returns that report a missing precondition.

Applied to the five work nodes, not to the two lifecycle nodes:
initialize_run does nothing worth timing, and finalize_run cannot appear in
the trace it is itself assembling.

An unexpected exception is re-raised rather than recorded as a trace entry —
instrumentation must never swallow an error. The node name is attached to the
exception so the outermost boundary can still report where it came from.
"""

from functools import wraps
from time import perf_counter
from typing import Any, Callable

from src.graph.state import AgentState


def timed_node(
    node_name: str,
) -> Callable[[Callable[..., AgentState]], Callable[..., AgentState]]:
    """Record how long a node took and whether it succeeded.

    The wrapped node returns its normal state update; this adds one entry to
    the node_trace accumulator. A node that raises is recorded as failed and
    the exception is re-raised — instrumentation must never swallow an error.
    """

    def decorate(
        node: Callable[..., AgentState],
    ) -> Callable[..., AgentState]:
        @wraps(node)
        def wrapper(self: Any, state: AgentState) -> AgentState:
            started_at = perf_counter()

            try:
                update = node(self, state)
            except Exception as error:
                raise _annotate(error, node_name, started_at) from None

            duration_ms = _elapsed_ms(started_at)

            error_stage = str(update.get("error_stage", "") or "")

            trace_entry: dict[str, Any] = {
                "node": node_name,
                "duration_ms": duration_ms,
                "ok": not error_stage,
            }

            if error_stage:
                trace_entry["error_stage"] = error_stage

            return {**update, "node_trace": [trace_entry]}

        return wrapper

    return decorate


def _elapsed_ms(started_at: float) -> float:
    """Return milliseconds elapsed, rounded for readable records."""

    return round((perf_counter() - started_at) * 1000, 2)


def _annotate(
    error: Exception,
    node_name: str,
    started_at: float,
) -> Exception:
    """Attach node context to an exception escaping a node."""

    error.graph_node = node_name  # type: ignore[attr-defined]
    error.graph_node_duration_ms = _elapsed_ms(  # type: ignore[attr-defined]
        started_at
    )
    return error
