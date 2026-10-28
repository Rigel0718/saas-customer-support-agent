import time
from collections.abc import Callable
from datetime import datetime, timezone

from app.schemas.support import FailureMetadata, TraceDTO


class ExecutionFailure(Exception):
    def __init__(self, code: str, *, failure_metadata: FailureMetadata | None = None):
        self.code = code
        self.failure_metadata = failure_metadata
        super().__init__(code)


class ExecutionBudget:
    def __init__(self, timeout_seconds: float, clock: Callable = time.monotonic):
        self.clock = clock
        self.deadline = clock() + timeout_seconds

    def remaining(self) -> float:
        seconds = self.deadline - self.clock()
        if seconds <= 0:
            raise ExecutionFailure("execution_timeout")
        return seconds


def add_trace(
    traces: list[TraceDTO],
    event_type: str,
    now: Callable = lambda: datetime.now(timezone.utc),
    **fields,
) -> TraceDTO:
    trace = TraceDTO(
        sequence=len(traces) + 1, event_type=event_type, created_at=now(), **fields
    )
    traces.append(trace)
    return trace
