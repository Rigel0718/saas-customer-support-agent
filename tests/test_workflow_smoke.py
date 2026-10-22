"""Workflow smoke tooling tests: no DB, server, credentials or live LLM required."""

from copy import deepcopy
from io import BytesIO
import json
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from unittest.mock import Mock
from uuid import UUID

import pytest

from scripts import workflow_smoke as smoke

DATE = "2026-10-22T00:00:00+00:00"


def sample():
    request = {
        "id": "00000000-0000-0000-0000-000000000999",
        "requester_user_id": smoke.MEMBER, "organization_id": smoke.ORG,
        "content": smoke.CONTENT, "status": "completed", "execution_mode": "workflow",
        "created_at": DATE, "started_at": DATE, "finished_at": DATE, "error_code": None,
        "diagnosis": {
            "outcome": "diagnosed", "summary": "권한 확인", "causes": ["member 권한"],
            "next_actions": ["관리자에게 요청"], "evidence": [
                {"trace_sequence": 2, "policy_document_id": None, "statement": "member"},
                {"trace_sequence": 7, "policy_document_id": "permissions", "statement": "정책"},
            ],
        },
    }
    events = [{"event_type": "execution_started"}]
    for step, (name, args) in enumerate(smoke.STEPS, 1):
        data = {"items": [{"document_id": "permissions"}]} if name == "search_policy" else {"role": "member"}
        events.append({
            "event_type": "tool_result", "step": step, "tool_name": name,
            "arguments": deepcopy(args), "duration_ms": 1,
            "observation": {"ok": True, "error_code": None, "trace_sequence": step + 1, "data": data},
        })
    events += [
        {"event_type": "llm_response", "step": 8, "model": "fake-model", "response_id": "fake-id", "duration_ms": 2},
        {"event_type": "final_result"}, {"event_type": "execution_completed"},
    ]
    traces = {"items": [{"sequence": i, "created_at": DATE, **event} for i, event in enumerate(events, 1)]}
    return request, traces


class Response(BytesIO):
    def __init__(self, value, status=200):
        super().__init__(json.dumps(value).encode())
        self.status = status


@pytest.fixture
def setup(tmp_path, monkeypatch):
    settings = smoke.Settings(
        "postgresql+psycopg://support:private-password@localhost/dev",
        {"owner-private": UUID("00000000-0000-0000-0000-000000000101"),
         "member-private": UUID(smoke.MEMBER)},
        openai_model="fake-model",  # No real API key, including for automated tests.
    )
    monkeypatch.setattr(smoke.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(smoke.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="fake-revision"))
    monkeypatch.setattr("builtins.input", lambda _: "RUN WORKFLOW")
    runner = smoke.WorkflowRunner(tmp_path)
    commands = []
    monkeypatch.setattr(runner, "command", lambda args: commands.append(args))
    runner.preparation_commands = commands
    process = Mock()
    process.poll.return_value = None
    monkeypatch.setattr(smoke.subprocess, "Popen", lambda *a, **k: process)
    request, traces = sample()
    pending = dict(request, status="pending", execution_mode=None, diagnosis=None, started_at=None, finished_at=None)
    calls = []
    behavior = {"diagnose": "success"}

    def fake(req, **kwargs):
        if isinstance(req, str):
            return Response({})  # Temporary Backend readiness.
        path = req.full_url.split(runner.base_url)[1]
        method = req.get_method()
        calls.append((method, path))
        if path.endswith("?mode=workflow"):
            assert req.get_header("Authorization") == "Bearer " + runner.token
            if behavior["diagnose"] == "timeout":
                raise URLError("member-private private-password")
            if behavior["diagnose"] == "http_error":
                raise HTTPError(req.full_url, 503, "private-password", {}, Response({"detail": {"code": "database_error"}}))
            return Response(request)
        if path == "/support/requests":
            assert req.get_header("Authorization") == "Bearer " + runner.token
            return Response(pending, 201)
        after = any("?mode=workflow" in p for _, p in calls)
        if path.endswith("/traces"):
            return Response(traces if after else {"items": []})
        if path.startswith("/support/requests/"):
            if behavior.get("already_running") and not after:
                return Response(dict(pending, status="running", execution_mode="workflow"))
            return Response(request if after else pending)
        if path.startswith("/users/"):
            return Response({"role": "member", "organization_id": smoke.ORG})
        return Response({})

    monkeypatch.setattr(runner.opener, "open", fake)
    return runner, calls, behavior


def test_success_reports_pending_semantic_review(setup):
    runner, calls, _ = setup
    runner.run()
    assert runner.preparation_commands == [
        ["docker", "compose", "up", "-d", "--wait", "postgres"],
        [smoke.sys.executable, "-m", "scripts.prepare_workflow_smoke_db"],
        [smoke.sys.executable, "-m", "app.seed"],
    ]
    assert runner.report() == 0
    report = (runner.directory / "report.md").read_text()
    assert "전체 판정: **의미 검토 대기**" in report
    assert "실패 경로 미발생" in report
    assert "미제공" in report
    assert sum("?mode=workflow" in path for _, path in calls) == 1
    assert all((runner.directory / name).exists() for name in (
        "created.json", "diagnosed.json", "retrieved.json", "traces.json", "report.md"))
    with pytest.raises(smoke.SmokeError, match="중복"):
        runner.http("duplicate", "POST", runner.path + "/diagnose?mode=workflow")
    assert sum("?mode=workflow" in path for _, path in calls) == 1


@pytest.mark.parametrize("failed_step", [0, 1, 2])
def test_preparation_failure_stops_before_backend_or_posts(setup, monkeypatch, failed_step):
    runner, calls, _ = setup
    commands = []
    def command(args):
        commands.append(args)
        if len(commands) == failed_step + 1:
            raise smoke.SmokeError("준비 명령 실패")
    start_backend = Mock()
    monkeypatch.setattr(runner, "command", command)
    monkeypatch.setattr(runner, "start_backend", start_backend)
    with pytest.raises(smoke.SmokeError, match="준비 명령 실패"):
        runner.run()
    assert len(commands) == failed_step + 1
    start_backend.assert_not_called()
    assert calls == []


@pytest.mark.parametrize("behavior", ["timeout", "http_error"])
def test_failed_diagnose_is_never_retried_and_gets_preserved(setup, behavior):
    runner, calls, options = setup
    options["diagnose"] = behavior
    with pytest.raises((URLError, smoke.SmokeError)):
        runner.run()
    runner.failure = "진단 HTTP 실패"
    assert runner.report() == 1
    assert sum("?mode=workflow" in path for _, path in calls) == 1
    assert sum(path == "/support/requests" for _, path in calls) == 1
    assert (runner.directory / "retrieved.json").exists()
    assert (runner.directory / "traces.json").exists()
    report = (runner.directory / "report.md").read_text()
    assert "private-password" not in report and "member-private" not in report
    assert "POST/GET 저장 일치 | 미검증" in report


@pytest.mark.parametrize("answer", ["", "yes", "EOF"])
def test_no_confirmation_means_no_posts(setup, monkeypatch, answer):
    runner, calls, _ = setup
    def confirm(_):
        if answer == "EOF":
            raise EOFError
        return answer
    monkeypatch.setattr("builtins.input", confirm)
    with pytest.raises(smoke.SmokeError, match="확인 없음"):
        runner.run()
    assert not any(method == "POST" for method, _ in calls)
    assert runner.report() == 2


def test_owner_only_settings_get_temporary_member_token(setup, monkeypatch):
    runner, calls, _ = setup
    settings = smoke.Settings("postgresql://support@localhost/dev", {"owner": UUID(smoke.ORG)})
    monkeypatch.setattr(smoke.Settings, "from_env", lambda: settings)
    environments = []
    def start(*args, **kwargs):
        environments.append(kwargs["env"])
        process = Mock()
        process.poll.return_value = None
        return process
    monkeypatch.setattr(smoke.subprocess, "Popen", start)
    runner.run()
    tokens = json.loads(environments[0]["DEV_BEARER_TOKENS"])
    assert tokens == {"owner": smoke.ORG, runner.token: smoke.MEMBER}
    assert settings.bearer_tokens == {"owner": UUID(smoke.ORG)}
    assert runner.report() == 0


def test_preexisting_execution_stops_without_diagnose(setup):
    runner, calls, options = setup
    options["already_running"] = True
    with pytest.raises(smoke.SmokeError, match="진단 전"):
        runner.run()
    assert not any("?mode=workflow" in path for _, path in calls)


@pytest.mark.parametrize("change,index", [
    ("mode", 0), ("order", 1), ("query", 1), ("scope", 1), ("observation", 1),
    ("llm_count", 2), ("dynamic", 3), ("status", 4), ("evidence", 5),
    ("sequence", 6), ("terminal", 6), ("post", 7),
])
def test_invalid_results_never_pass(change, index):
    request, traces = sample()
    post = deepcopy(request)
    items = traces["items"]
    if change == "mode":
        request["execution_mode"] = "agent"
    elif change == "order":
        items[1], items[2] = items[2], items[1]
    elif change == "query":
        items[5]["arguments"] = {"query": "다른 검색"}
    elif change == "scope":
        items[1]["arguments"] = {"user_id": smoke.ORG}
    elif change == "observation":
        items[1]["observation"]["ok"] = False
    elif change == "llm_count":
        items.insert(9, deepcopy(items[8]))
    elif change == "dynamic":
        items[8]["tool_name"] = "search_policy"
    elif change == "status":
        request["status"] = "running"
    elif change == "evidence":
        request["diagnosis"]["evidence"][1]["policy_document_id"] = "unseen"
    elif change == "sequence":
        items[3]["sequence"] = 99
    elif change == "terminal":
        items.pop()
    elif change == "post":
        post["content"] = "different"
    assert smoke.assess(post, request, traces)[smoke.LABELS[index]][0] == "Fail"


@pytest.mark.parametrize("unsafe,missing", [(False, False), (True, False), (False, True)])
def test_failure_error_and_preserved_trace(unsafe, missing):
    request, traces = sample()
    code = "unsafe raw exception" if unsafe else "llm_error"
    request.update(status="failed", diagnosis=None, error_code=code)
    traces["items"][-1].update(event_type="execution_failed", error_code=code)
    if missing:
        traces["items"] = []
    result = smoke.assess(request, request, traces)
    assert result[smoke.LABELS[8]][0] == ("Fail" if unsafe or missing else "Pass")
    assert result[smoke.LABELS[4]][0] == "Fail"


def test_backend_disconnection_generates_report(setup, monkeypatch):
    runner, _, _ = setup
    def disconnected(*a, **k):
        raise URLError("private-password")
    monkeypatch.setattr(runner.opener, "open", disconnected)
    monkeypatch.setattr(smoke.time, "sleep", lambda _: None)
    with pytest.raises(smoke.SmokeError, match="대기 시간 초과"):
        runner.run()
    runner.failure = "Backend 연결 실패"
    assert runner.report() == 1
    assert "Backend 연결 실패" in (runner.directory / "report.md").read_text()


def test_secret_redaction_in_all_saved_files(setup):
    runner, _, _ = setup
    runner.run()
    runner.write("redacted.json", {"Authorization": "Bearer unknown", "nested": {"password": "unknown"},
                                   "text": "member-private private-password"})
    runner.report()
    for path in runner.directory.iterdir():
        text = path.read_text()
        assert "member-private" not in text and "private-password" not in text
    assert "unknown" not in (runner.directory / "redacted.json").read_text()


def test_report_write_failure_returns_safe_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(smoke, "ROOT", tmp_path)
    monkeypatch.setattr(smoke.WorkflowRunner, "run", lambda self: None)
    def fail(self, name, value):
        raise OSError("private-error")
    monkeypatch.setattr(smoke.WorkflowRunner, "write", fail)
    assert smoke.main() == 1
    captured = capsys.readouterr()
    assert "저장 불가" in captured.err and "private-error" not in captured.err


def test_main_suppresses_exception_contents(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke, "ROOT", tmp_path)
    def fail(self):
        raise ValueError("private-error")
    monkeypatch.setattr(smoke.WorkflowRunner, "run", fail)
    assert smoke.main() == 1
    report = next(tmp_path.glob(".smoke-results/workflow-run-*/report.md")).read_text()
    assert "ValueError" in report and "private-error" not in report


@pytest.mark.parametrize("result", ["success", "failure", "interrupt"])
def test_main_always_stops_temporary_backend(setup, monkeypatch, result):
    runner, _, _ = setup
    process = Mock()
    process.poll.return_value = None
    def run(self):
        self.process = process
        if result == "interrupt":
            raise KeyboardInterrupt
        if result == "failure":
            raise smoke.SmokeError("Backend 시작 실패")
    monkeypatch.setattr(smoke, "ROOT", runner.directory)
    monkeypatch.setattr(smoke.WorkflowRunner, "run", run)
    assert smoke.main() == (2 if result == "success" else 1)
    process.terminate.assert_called_once()
    process.wait.assert_called_once()


def test_redirect_is_refused():
    with pytest.raises(smoke.SmokeError):
        smoke.NoRedirect().redirect_request(None, None, None, None, None, None)


def test_invalid_json_diagnose_still_recovers(setup, monkeypatch):
    runner, calls, _ = setup
    original = runner.opener.open
    def invalid(req, **kwargs):
        if isinstance(req, str):
            return original(req, **kwargs)
        if "?mode=workflow" in req.full_url:
            calls.append(("POST", req.full_url))
            response = Response({})
            response.seek(0)
            response.truncate()
            response.write(b"not-json")
            response.seek(0)
            return response
        return original(req, **kwargs)
    monkeypatch.setattr(runner.opener, "open", invalid)
    with pytest.raises(smoke.SmokeError, match="JSON"):
        runner.run()
    assert (runner.directory / "retrieved.json").exists()
    assert (runner.directory / "traces.json").exists()
    assert runner.diagnosis_attempted


def test_creation_write_failure_recovers_same_request(setup, monkeypatch):
    runner, calls, _ = setup
    original = runner.write
    def fail_creation(name, value):
        if name == "created.json":
            raise OSError("private-file-error")
        return original(name, value)
    monkeypatch.setattr(runner, "write", fail_creation)
    with pytest.raises(OSError):
        runner.run()
    runner.recover()
    assert runner.path.endswith("000000000999")
    assert (runner.directory / "retrieved.json").exists()
    assert sum(path == "/support/requests" for _, path in calls) == 1
    assert not runner.diagnosis_attempted


def test_malformed_trace_report_never_passes(setup):
    runner, _, _ = setup
    runner.run()
    runner.responses["traces"] = {"items": ["invalid"]}
    assert runner.report() == 1
    assert "Trace 응답 형식 오류" in (runner.directory / "report.md").read_text()


def test_get_failure_after_diagnose_leaves_checks_unverified(setup, monkeypatch):
    runner, calls, _ = setup
    original = runner.opener.open
    def disconnected(req, **kwargs):
        if isinstance(req, str):
            return original(req, **kwargs)
        if (req.get_method() == "GET" and "/support/requests/" in req.full_url
                and runner.diagnosis_attempted):
            raise URLError("private-error")
        return original(req, **kwargs)
    monkeypatch.setattr(runner.opener, "open", disconnected)
    runner.run()
    assert runner.report() == 2
    assert sum("?mode=workflow" in path for _, path in calls) == 1
    assert "POST/GET 저장 일치 | 미검증" in (runner.directory / "report.md").read_text()


@pytest.mark.parametrize("name,value", [
    ("retrieved", ["malformed"]),
    ("retrieved", {"status": "completed"}),
    ("traces", {"items": ["invalid"]}),
])
def test_malformed_responses_still_produce_report(setup, name, value):
    runner, _, _ = setup
    runner.run()
    runner.responses[name] = value
    assert runner.report() == 1
    assert (runner.directory / "report.md").exists()
