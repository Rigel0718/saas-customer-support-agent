import json

import pytest
from sqlalchemy.exc import OperationalError

from app.diagnosis.runtime import ExecutionBudget, ExecutionFailure, add_trace
from app.diagnosis.tools import UserArguments, execute_observed_tool
from app.seed import uid
from app.services import customer
from tests.test_support_models import NOW


@pytest.mark.parametrize("elapsed", [2.0, 2.5])
def test_budget_expires_at_deadline(elapsed):
    ticks = [10.0]
    budget = ExecutionBudget(2, clock=lambda: ticks[0])
    assert budget.remaining() == 2
    ticks[0] = 11.5
    assert budget.remaining() == 0.5
    ticks[0] = 10 + elapsed
    with pytest.raises(ExecutionFailure, match="execution_timeout") as exc:
        budget.remaining()
    assert exc.value.code == "execution_timeout"


def test_trace_sequence_spans_events_with_injected_utc():
    traces = []
    started = add_trace(traces, "execution_started", lambda: NOW)
    llm = add_trace(traces, "llm_response", lambda: NOW, step=1)
    observation = add_trace(traces, "tool_result", lambda: NOW, step=1)
    assert traces == [started, llm, observation]
    assert [t.sequence for t in traces] == [1, 2, 3]
    assert all(t.created_at == NOW for t in traces)


@pytest.mark.parametrize(
    "name,arguments,code",
    [
        ("get_user", {"user_id": str(uid(0x103))}, None),
        ("get_user", {"user_id": str(uid(0x201))}, "not_found"),
        ("refund", {"private": "untrusted"}, "unknown_tool"),
        ("get_user", {"user_id": "untrusted"}, "invalid_arguments"),
    ],
)
def test_observed_tool_links_each_result_to_its_trace(session, name, arguments, code):
    traces = []
    add_trace(traces, "execution_started", lambda: NOW)
    add_trace(traces, "llm_response", lambda: NOW, step=1)
    result = execute_observed_tool(
        name,
        json.dumps(arguments),
        session,
        customer.CallerContext(uid(0x102), uid(1), "admin"),
        [],
        traces,
        ExecutionBudget(60, lambda: 0),
        step=1,
        call_id="observed-call",
        now=lambda: NOW,
    )
    assert len(traces) == 3
    trace = traces[-1]
    assert trace.sequence == result.observation["trace_sequence"] == 3
    assert trace.observation == result.observation
    assert trace.arguments == result.arguments
    assert trace.error_code == code
    assert trace.call_id == "observed-call" and trace.step == 1
    if code in ("unknown_tool", "invalid_arguments"):
        assert trace.arguments is None
        assert "untrusted" not in trace.model_dump_json()


def test_observed_tool_validates_arguments_once(session, monkeypatch):
    original = UserArguments.model_validate_json
    calls = []

    def validate(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(UserArguments, "model_validate_json", validate)
    raw = json.dumps({"user_id": str(uid(0x103))})
    result = execute_observed_tool(
        "get_user",
        raw,
        session,
        customer.CallerContext(uid(0x102), uid(1), "admin"),
        [],
        [],
        ExecutionBudget(60, lambda: 0),
        step=1,
    )
    assert result.observation["ok"] and calls == [raw]


@pytest.mark.parametrize("database_error", [True, False])
def test_timeout_setup_failure_is_traced_and_preserves_db_exception(
    session, monkeypatch, database_error
):
    error = (
        OperationalError("private SQL", {}, Exception("private connection"))
        if database_error
        else RuntimeError("private failure")
    )
    ticks = [0.0]

    def fail(*args, **kwargs):
        ticks[0] = 0.25
        raise error

    monkeypatch.setattr(session, "execute", fail)
    traces = []
    with pytest.raises(OperationalError if database_error else ExecutionFailure) as exc:
        execute_observed_tool(
            "get_user",
            json.dumps({"user_id": str(uid(0x103))}),
            session,
            customer.CallerContext(uid(0x102), uid(1), "admin"),
            [],
            traces,
            ExecutionBudget(60, lambda: ticks[0]),
            step=1,
            now=lambda: NOW,
        )
    if database_error:
        assert exc.value is error
    else:
        assert exc.value.code == "tool_error"
    assert len(traces) == 1 and traces[0].observation is None
    assert traces[0].error_code == ("database_error" if database_error else "tool_error")
    assert traces[0].arguments == {"user_id": str(uid(0x103))}
    assert traces[0].duration_ms == 250
    assert "private" not in traces[0].model_dump_json()
