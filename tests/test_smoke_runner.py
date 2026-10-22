"""Smoke automation verification without Docker, a database, or live LLM calls."""

from copy import deepcopy
from io import BytesIO
import json
from types import SimpleNamespace
from urllib.error import URLError
from unittest.mock import Mock
from uuid import UUID

import pytest

from scripts import support_agent_smoke as smoke

DATE = "2026-10-22T00:00:00+00:00"


def sample():
    request = {
        "id": "00000000-0000-0000-0000-000000000999",
        "requester_user_id": smoke.MEMBER, "organization_id": smoke.ORG,
        "content": smoke.CONTENT, "status": "completed",
        "created_at": DATE, "started_at": DATE, "finished_at": DATE,
        "error_code": None,
        "diagnosis": {
            "outcome": "diagnosed", "summary": "member는 초대 권한이 없습니다.",
            "causes": ["초대 권한 부족"], "next_actions": ["관리자에게 요청하세요."],
            "evidence": [
                {"trace_sequence": 3, "policy_document_id": None, "statement": "member 역할"},
                {"trace_sequence": 5, "policy_document_id": "permissions", "statement": "초대 정책"},
            ],
        },
    }
    events = [
        {"event_type": "execution_started"},
        {"event_type": "llm_response", "step": 1, "model": "fake-model", "response_id": "fake-1"},
        {"event_type": "tool_result", "step": 1, "tool_name": "get_user", "call_id": "call-1",
         "arguments": {"user_id": smoke.MEMBER},
         "observation": {"ok": True, "error_code": None, "trace_sequence": 3,
                         "data": {"role": "member", "organization_id": smoke.ORG}}},
        {"event_type": "llm_response", "step": 2, "model": "fake-model", "response_id": "fake-2"},
        {"event_type": "tool_result", "step": 2, "tool_name": "search_policy", "call_id": "call-2",
         "arguments": {"query": "초대"},
         "observation": {"ok": True, "error_code": None, "trace_sequence": 5,
                         "data": {"items": [{"document_id": "permissions"}]}}},
        {"event_type": "llm_response", "step": 3, "model": "fake-model", "response_id": "fake-3"},
        {"event_type": "final_result"},
        {"event_type": "execution_completed"},
    ]
    traces = {"items": [{"sequence": i, "created_at": DATE, "duration_ms": 1, **e}
                        for i, e in enumerate(events, 1)]}
    return request, traces


def test_assessment_leaves_semantic_review_pending():
    request, traces = sample()
    results = smoke.assess(request, deepcopy(request), traces)
    assert [r[0] for r in results] == ["Pass", "Pass", "미검증", "Pass", "Pass"]


@pytest.mark.parametrize("change,index", [
    ("policy_reference", 2), ("sequence", 3), ("post_mismatch", 4),
    ("role", 1), ("scope", 1), ("no_connection", 0),
])
def test_bad_result_is_not_reported_as_pass(change, index):
    request, traces = sample()
    post = deepcopy(request)
    if change == "policy_reference":
        request["diagnosis"]["evidence"][1]["policy_document_id"] = "not-observed"
    elif change == "sequence":
        traces["items"][-1]["sequence"] = 50
    elif change == "post_mismatch":
        post["content"] = "different"
    elif change == "role":
        traces["items"][2]["observation"]["data"]["role"] = "owner"
    elif change == "scope":
        traces["items"][2]["arguments"]["user_id"] = str(smoke.ORG)
    elif change == "no_connection":
        for t in traces["items"]:
            t.pop("response_id", None)
    assert smoke.assess(post, request, traces)[index][0] == "Fail"


def test_single_step_does_not_prove_followup():
    request, traces = sample()
    for t in traces["items"]:
        if "step" in t:
            t["step"] = 1
    assert smoke.assess(request, request, traces)[1][0] == "미검증"


def test_failed_execution_preserves_connection_success():
    request, traces = sample()
    request.update(status="failed", diagnosis=None, error_code="llm_error")
    traces["items"][-1]["event_type"] = "execution_failed"
    results = smoke.assess(request, request, traces)
    assert results[0][0] == "Pass"
    assert all(results[i][0] == "Fail" for i in (2, 3, 4))


class Response(BytesIO):
    def __init__(self, data, status=200):
        super().__init__(json.dumps(data).encode())
        self.status = status


@pytest.mark.parametrize("diagnose_result", ["success", "timeout", "http_error"])
def test_runner_collects_report_and_never_retries_post(tmp_path, monkeypatch, diagnose_result):
    request, traces = sample()
    pending = dict(request, status="pending", diagnosis=None, started_at=None, finished_at=None)
    settings = smoke.Settings(
        "postgresql+psycopg://support:private-password@localhost/support_dev",
        {"private-bearer": UUID(smoke.MEMBER)},
        openai_api_key="private-api-key", openai_model="fake-model",
    )
    monkeypatch.setattr(smoke.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(smoke.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="fake-revision\n"))
    process = Mock()
    process.poll.return_value = None
    monkeypatch.setattr(smoke.subprocess, "Popen", lambda *a, **k: process)
    commands = []
    runner = smoke.Runner(tmp_path)
    monkeypatch.setattr(runner, "command", lambda args: commands.append(args))
    calls = []

    def open_fake(req, **kwargs):
        if isinstance(req, str):
            return Response({})  # Server readiness.
        path = req.full_url.split(runner.base_url)[1]
        method = req.get_method()
        calls.append((method, path))
        if path.endswith("/diagnose"):
            if diagnose_result == "timeout":
                raise URLError("private-api-key")
            if diagnose_result == "http_error":
                return Response({"detail": {"code": "service_unavailable"}}, 503)
            return Response(request)
        if path == "/support/requests":
            return Response(pending, 201)
        if path.endswith("/traces"):
            return Response(traces if any(p.endswith("/diagnose") for _, p in calls) else {"items": []})
        if path.startswith("/support/requests/"):
            return Response(request if any(p.endswith("/diagnose") for _, p in calls) else pending)
        if path.startswith("/users/"):
            return Response({"role": "member", "organization_id": smoke.ORG})
        return Response({"expires_at": DATE})

    monkeypatch.setattr(runner.opener, "open", open_fake)
    try:
        runner.run()
    except (smoke.SmokeError, URLError) as exc:
        runner.failure = type(exc).__name__
    finally:
        runner.stop()
    code = runner.report()
    assert code == (0 if diagnose_result == "success" else 1)
    assert sum(path.endswith("/diagnose") for _, path in calls) == 1
    assert (tmp_path / "retrieved.json").exists()
    assert (tmp_path / "traces.json").exists()
    assert len(commands) == 3
    process.terminate.assert_called_once()
    report = (tmp_path / "report.md").read_text()
    assert all(f"ST-{i:02d}" in report for i in range(1, 6))
    assert "private-api-key" not in report and "private-password" not in report
    runner.write("redacted.json", {"text": "private-api-key private-bearer private-password " + runner.token})
    assert "private-" not in (tmp_path / "redacted.json").read_text()


def test_setup_failure_still_generates_report_without_secret(tmp_path, monkeypatch):
    runner = smoke.Runner(tmp_path)
    runner.failure = "DB 준비 실패"
    assert runner.report() == 1
    text = (tmp_path / "report.md").read_text()
    assert "DB 준비 실패" in text and "미검증" in text


def test_main_captures_setup_error_without_raw_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke, "ROOT", tmp_path)

    def fail(self):
        raise ValueError("sensitive-error-content")

    monkeypatch.setattr(smoke.Runner, "run", fail)
    assert smoke.main() == 1
    report = next(tmp_path.glob(".smoke-results/run-*/report.md")).read_text()
    assert "ValueError" in report and "sensitive-error-content" not in report


def test_malformed_traces_still_produce_failed_report(tmp_path):
    runner = smoke.Runner(tmp_path)
    runner.responses["traces"] = {"items": ["invalid"]}
    assert runner.report() == 1
    assert "Trace 응답 형식 오류" in (tmp_path / "report.md").read_text()


def test_real_backend_starts_on_reserved_socket_without_database_or_llm(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://support:unused@127.0.0.1:1/unused"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_MODEL", "")
    runner = smoke.Runner(tmp_path)
    runner.settings = smoke.Settings(
        "postgresql+psycopg://support:unused@127.0.0.1:1/unused", {}
    )
    try:
        runner.start_backend()
        with runner.opener.open(runner.base_url + "/openapi.json", timeout=5) as response:
            schema = json.load(response)
        assert "/support/requests/{request_id}/diagnose" in schema["paths"]
    finally:
        runner.stop()
    assert runner.process.poll() is not None
