"""실제 SDK/HTTPX와 localhost TCP 연결로 deadline 취소를 검증한다."""

import asyncio
import json
import socket
import time
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI

from app.agent.loop import run_agent
from app.config import Settings
from app.diagnosis.llm import responses_create
from app.diagnosis.runtime import ExecutionFailure
from app.seed import uid
from app.services import customer, support
from tests.conftest import headers
from tests.test_agent_loop import evidence, final, function, response
from tests.test_support_models import NOW


class SlowUpload(httpx.AsyncByteStream):
    def __init__(self, body):
        self.body = body

    async def __aiter__(self):
        for offset in range(0, len(self.body), 8):
            yield self.body[offset : offset + 8]
            await asyncio.sleep(0.04)


class UploadTransport(httpx.AsyncBaseTransport):
    """SDK의 JSON을 작은 조각으로 나눠 실제 TCP transport에 전달한다."""

    def __init__(self):
        self.transport = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request):
        request.stream = SlowUpload(await request.aread())
        return await self.transport.handle_async_request(request)

    async def aclose(self):
        await self.transport.aclose()


@contextmanager
def local_responses(monkeypatch, mode, script=None):
    async def prepare_sdk():
        # 최초 Responses 지연 로딩이 짧은 Budget을 소진하면 HTTP I/O에
        # 진입하지 못한다. 통신 없이 준비하여 실제 연결의 취소를 검증한다.
        async with AsyncOpenAI(
            api_key="fake-local-key",
            base_url="http://127.0.0.1/v1",
            http_client=httpx.AsyncClient(trust_env=False),
        ) as client:
            _ = client.responses

    asyncio.run(prepare_sdk())
    records = []
    disconnected = Event()
    stopped = Event()
    clients = []
    payloads = script or [response([function()])]

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            self.connection.settimeout(5)
            record = {
                "received": 0,
                "retry_count": self.headers.get("x-stainless-retry-count"),
            }
            records.append(record)
            try:
                length = int(self.headers["Content-Length"])
                body = self.rfile.read(length)
                record["received"] = len(body)
                record["expected"] = length
                if len(body) != length:
                    disconnected.set()
                    return
                record["payload"] = json.loads(body)

                # 서버도 EOF를 확인하여 클라이언트 I/O가 실제 종료됐음을 검증한다.
                def observe_close():
                    try:
                        if self.connection.recv(1) == b"":
                            disconnected.set()
                    except (ConnectionResetError, OSError):
                        disconnected.set()

                if mode in ("headers", "trickle"):
                    Thread(target=observe_close, daemon=True).start()
                if mode == "headers":
                    disconnected.wait(5)
                    return
                if mode == "error":
                    self.send_response(503)
                    output = {
                        "error": {"message": "local failure", "type": "server_error"}
                    }
                else:
                    self.send_response(200)
                    output = payloads[len(records) - 1] | {
                        "object": "response",
                        "created_at": 1768435200,
                    }
                data = json.dumps(output).encode()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if mode == "trickle":
                    for offset in range(0, len(data), 8):
                        if disconnected.is_set():
                            return
                        self.wfile.write(data[offset : offset + 8])
                        self.wfile.flush()
                        if disconnected.wait(0.04):
                            return
                else:
                    self.wfile.write(data)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                disconnected.set()
            finally:
                if mode in ("headers", "trickle", "upload"):
                    stopped.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    thread.start()

    def sdk(**kwargs):
        client = httpx.AsyncClient(
            trust_env=False, transport=UploadTransport() if mode == "upload" else None
        )
        clients.append(client)
        return AsyncOpenAI(
            **kwargs,
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            http_client=client,
        )

    with monkeypatch.context() as patch:
        patch.setattr("app.diagnosis.llm.AsyncOpenAI", sdk)
        try:
            yield SimpleNamespace(
                records=records,
                disconnected=disconnected,
                stopped=stopped,
                clients=clients,
            )
        finally:
            disconnected.set()
            server.shutdown()
            server.server_close()
            thread.join(2)


def settings(timeout=1):
    return Settings(
        "unused",
        {},
        openai_api_key="fake-local-key",
        openai_model="fake-local-model",
        agent_timeout_seconds=timeout,
    )


def run_without_tools(configuration, traces, create=None):
    return run_agent(
        None,
        customer.CallerContext(uid(0x101), uid(1), "owner"),
        SimpleNamespace(requester_user_id=uid(0x101), content="초대 문의"),
        configuration,
        traces,
        create=create,
        now=lambda: NOW,
    )


@pytest.mark.parametrize("mode", ["headers", "trickle", "upload"])
def test_deadline_cancels_real_http_io(monkeypatch, mode):
    with local_responses(monkeypatch, mode) as server:
        traces = []
        before = time.monotonic()
        with pytest.raises(ExecutionFailure, match="execution_timeout"):
            run_without_tools(settings(), traces)
        elapsed = time.monotonic() - before
        assert (
            elapsed < 1.7
        )  # 스케줄링·SDK 정리 여유이며 본문을 끝까지 기다리지는 않는다.
        assert len(server.records) == 1, "취소를 검증할 HTTP 요청이 서버에 도달해야 한다"
        assert server.disconnected.wait(1), "서버에서 TCP EOF/연결 종료를 확인해야 한다"
        assert server.stopped.wait(1)
        assert server.records[0]["retry_count"] == "0"
        assert all(client.is_closed for client in server.clients)
        assert (
            traces[-1].event_type == "llm_response"
            and traces[-1].error_code == "execution_timeout"
        )
        assert not any(trace.event_type == "tool_result" for trace in traces)
        if mode == "upload":
            assert 0 < server.records[0]["received"] < server.records[0]["expected"]


def test_phase_timeout_before_deadline_is_llm_error(monkeypatch):
    config = settings(3)

    def short_timeout(**kwargs):
        return responses_create(config, budget_seconds=3, **(kwargs | {"timeout": 0.1}))

    with local_responses(monkeypatch, "headers") as server:
        traces = []
        with pytest.raises(ExecutionFailure, match="llm_error"):
            run_without_tools(config, traces, create=short_timeout)
        assert server.disconnected.wait(1)
        assert len(server.records) == 1 and all(
            client.is_closed for client in server.clients
        )
        assert traces[-1].error_code == "llm_error"


def test_sdk_http_error_has_no_retry(monkeypatch):
    with local_responses(monkeypatch, "error") as server:
        traces = []
        with pytest.raises(ExecutionFailure, match="llm_error"):
            run_without_tools(settings(3), traces)
        assert len(server.records) == 1 and server.records[0]["retry_count"] == "0"
        assert all(client.is_closed for client in server.clients)


def test_real_sdk_normal_tool_loop(session, monkeypatch):
    script = [
        response([function(call_id="subscription")]),
        final("diagnosed", [evidence(3)]),
    ]
    with local_responses(monkeypatch, "normal", script) as server:
        traces = []
        context = customer.authenticate(session, uid(0x101))
        raw = run_agent(
            session,
            context,
            SimpleNamespace(requester_user_id=uid(0x101), content="문의"),
            settings(5),
            traces,
            now=lambda: NOW,
        )
        assert support.validate_diagnosis(raw, traces).outcome == "diagnosed"
        assert len(server.records) == 2
        second = server.records[1]["payload"]
        outputs = [
            item
            for item in second["input"]
            if item.get("type") == "function_call_output"
        ]
        assert outputs[0]["call_id"] == "subscription"
        assert json.loads(outputs[0]["output"])["data"]["status"] == "active"
        assert all(record["retry_count"] == "0" for record in server.records)
        assert all(client.is_closed for client in server.clients)


def test_http_cancel_saves_safe_failure_without_more_calls(durable_app, monkeypatch):
    durable_app.state.settings = replace(
        durable_app.state.settings,
        openai_api_key="fake-local-key",
        openai_model="fake-local-model",
        agent_timeout_seconds=1,
    )
    tools = []

    def unexpected_tool(*args, **kwargs):
        tools.append(True)
        raise AssertionError("deadline 이후 Tool 실행")

    monkeypatch.setattr("app.agent.loop.execute_observed_tool", unexpected_tool)
    with (
        local_responses(monkeypatch, "trickle") as server,
        TestClient(durable_app) as client,
    ):
        data = client.post(
            "/support/requests", headers=headers(), json={"content": "초대 문의"}
        ).json()
        path = f"/support/requests/{data['id']}"
        result = client.post(path + "/diagnose", headers=headers())
        assert result.status_code == 200
        assert (
            result.json()["status"] == "failed"
            and result.json()["error_code"] == "execution_timeout"
        )
        assert result.json()["diagnosis"] is None and tools == []
        assert server.disconnected.wait(1) and len(server.records) == 1
        saved = client.get(path, headers=headers()).json()
        assert saved == result.json()
        traces = client.get(path + "/traces", headers=headers()).json()["items"]
        assert traces[-1]["event_type"] == "execution_failed"
        assert traces[-1]["error_code"] == "execution_timeout"
        assert "fake-local-key" not in json.dumps(traces)
        assert client.post(path + "/diagnose", headers=headers()).status_code == 409
        assert len(server.records) == 1


def test_database_pool_wait_can_exceed_remaining_budget(engine):
    """현재 동기 QueuePool의 남은 한계를 실제 DB 연결로 기록한다."""
    from sqlalchemy import create_engine
    from sqlalchemy.exc import TimeoutError as PoolTimeout
    from sqlalchemy.orm import Session
    from tests.test_agent_loop import ScriptedResponses

    assert engine.pool.timeout() == 5
    # 테스트에서는 같은 QueuePool 경로의 대기를 0.3초로 줄인다.
    waiting = create_engine(
        engine.url,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.3,
        connect_args={"connect_timeout": 5},
    )
    try:
        with waiting.connect(), Session(waiting) as session:
            fake = ScriptedResponses([response([function()])])
            traces = []
            before = time.monotonic()
            with pytest.raises(PoolTimeout):
                run_agent(
                    session,
                    customer.CallerContext(uid(0x101), uid(1), "owner"),
                    SimpleNamespace(requester_user_id=uid(0x101), content="문의"),
                    settings(0.05),
                    traces,
                    create=fake,
                )
            elapsed = time.monotonic() - before
            assert elapsed >= 0.25  # 0.05초 Agent budget보다 긴 실제 pool 대기.
            assert len(fake.calls) == 1
            assert (
                traces[-1].event_type == "tool_result"
                and traces[-1].error_code == "database_error"
            )
    finally:
        waiting.dispose()
