import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.orm import Session

from app.diagnosis.llm import responses_create
from app.diagnosis.runtime import ExecutionFailure
from app.seed import uid
from app.services import customer
from app.workflow.runner import run_workflow
from tests.conftest import headers
from tests.test_agent_deadline import local_responses, settings
from tests.test_agent_loop import ScriptedResponses, final
from tests.test_support_models import NOW


def run(session, config, traces, create=None):
    return run_workflow(
        session,
        customer.CallerContext(uid(0x101), uid(1), "owner"),
        SimpleNamespace(requester_user_id=uid(0x101), content="초대 문의"),
        config,
        traces,
        create=create,
        now=lambda: NOW,
    )


@pytest.mark.parametrize("mode", ["headers", "trickle", "upload"])
def test_workflow_cancels_local_sdk_io(session, monkeypatch, mode):
    config = replace(settings(), workflow_timeout_seconds=3)
    script = [final(summary="시간 초과 검증을 위한 긴 응답" * 200)]
    with local_responses(monkeypatch, mode, script) as server:
        traces = []
        before = time.monotonic()
        with pytest.raises(ExecutionFailure, match="execution_timeout"):
            run(session, config, traces)
        assert time.monotonic() - before < 3.7
        assert server.disconnected.wait(1) and server.stopped.wait(1)
        assert len(server.records) == 1 and server.records[0]["retry_count"] == "0"
        assert all(c.is_closed for c in server.clients)
        assert len([t for t in traces if t.event_type == "tool_result"]) == 9
        assert traces[-1].event_type == "llm_response"
        assert traces[-1].error_code == "execution_timeout"


@pytest.mark.parametrize("mode", ["error", "headers", "normal"])
def test_workflow_sdk_failure_and_no_retry(session, monkeypatch, mode):
    config = replace(settings(3), workflow_timeout_seconds=3)

    def short_timeout(**kwargs):
        return responses_create(config, budget_seconds=3, **(kwargs | {"timeout": 0.1}))

    with local_responses(monkeypatch, mode, [final()]) as server:
        traces = []
        if mode == "normal":
            raw = run(session, config, traces)
            assert "needs_information" in raw
            assert "tools" not in server.records[0]["payload"]
        else:
            with pytest.raises(ExecutionFailure, match="llm_error"):
                run(
                    session,
                    config,
                    traces,
                    short_timeout if mode == "headers" else None,
                )
        assert len(server.records) == 1 and server.records[0]["retry_count"] == "0"
        assert all(c.is_closed for c in server.clients)


def test_workflow_timeout_is_saved(durable_app, monkeypatch):
    durable_app.state.settings = replace(
        settings(),
        database_url=durable_app.state.settings.database_url,
        bearer_tokens=durable_app.state.settings.bearer_tokens,
        workflow_timeout_seconds=1,
    )
    with (
        local_responses(monkeypatch, "trickle") as server,
        TestClient(durable_app) as client,
    ):
        req = client.post(
            "/support/requests", headers=headers(), json={"content": "문의"}
        ).json()
        path = f"/support/requests/{req['id']}"
        data = client.post(path + "/diagnose?mode=workflow", headers=headers()).json()
        assert data["status"] == "failed" and data["error_code"] == "execution_timeout"
        assert data["diagnosis"] is None and data["execution_mode"] == "workflow"
        assert client.get(path, headers=headers()).json() == data
        traces = client.get(path + "/traces", headers=headers()).json()["items"]
        assert traces[-1]["event_type"] == "execution_failed"
        assert len(server.records) == 1 and server.disconnected.wait(1)
        assert (
            client.post(path + "/diagnose?mode=agent", headers=headers()).status_code
            == 409
        )


def test_workflow_db_timeout_separate_from_budget(engine):
    assert engine.pool.timeout() == 5
    waiting = create_engine(
        engine.url,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.3,
        connect_args={"connect_timeout": 5},
    )
    try:
        with waiting.connect(), Session(waiting) as session:
            fake = ScriptedResponses([])
            traces = []
            before = time.monotonic()
            with pytest.raises(PoolTimeout):
                run(
                    session,
                    replace(settings(), workflow_timeout_seconds=0.05),
                    traces,
                    fake,
                )
            assert time.monotonic() - before >= 0.25
            assert fake.calls == []
            assert traces[-1].event_type == "tool_result"
            assert traces[-1].error_code == "database_error"
    finally:
        waiting.dispose()


def test_workflow_statement_timeout_is_finite(session):
    run(
        session,
        replace(settings(), workflow_timeout_seconds=2),
        [],
        ScriptedResponses([final()]),
    )
    assert (
        0
        < int(
            session.scalar(
                text(
                    "SELECT EXTRACT(EPOCH FROM current_setting('statement_timeout')::interval) * 1000"
                )
            )
        )
        <= 2000
    )
