import json

import pytest

from evaluation.results import ResultStore
from evaluation.runner import run_collection
from scripts import evaluate
from tests.test_agent_loop import final
from tests.test_evaluation_fixtures import evaluation_database  # noqa: F401


@pytest.mark.parametrize("request_timeout", [40, 120])
def test_cli_timeouts_reach_both_real_runners_and_saved_results(
    evaluation_db, monkeypatch, tmp_path, request_timeout
):
    engine, _ = evaluation_db
    monkeypatch.setenv("OPENAI_MODEL", "synthetic-model")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    monkeypatch.setenv("EVALUATION_DATABASE_URL", "postgresql://db.invalid/evaluation")
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECONDS", "20")
    monkeypatch.setenv("AGENT_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("WORKFLOW_TIMEOUT_SECONDS", "70")
    # Preparation is redirected only to the fixture's isolated test-owned schema.
    monkeypatch.setattr(evaluate, "build_engine", lambda *a: engine)
    monkeypatch.setattr(evaluate, "verify_database", lambda *a: None)
    monkeypatch.setattr(
        evaluate, "create_run_schema", lambda *a: (engine, "test_owned_schema")
    )
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return final()

    def collect(store, guard, settings):
        assert settings.llm_request_timeout_seconds == request_timeout
        assert settings.agent_timeout_seconds == settings.workflow_timeout_seconds == 90
        run_collection(store, guard, settings, create=fake)

    monkeypatch.setattr(evaluate, "run_collection", collect)
    path = tmp_path / "run"
    assert (
        evaluate.main(
            [
                "run",
                "--allow-live-diagnosis",
                "--output-dir",
                str(path),
                "--llm-request-timeout-seconds",
                str(request_timeout),
                "--execution-timeout-seconds",
                "90",
            ]
        )
        == 0
    )
    restored = ResultStore.load(path)
    assert (
        restored.manifest.configuration["llm_request_timeout_seconds"]
        == request_timeout
    )
    assert restored.manifest.configuration["agent_timeout_seconds"] == 90
    assert restored.manifest.configuration["workflow_timeout_seconds"] == 90
    assert len(calls) == len(restored.executions) == 10
    assert {e.mode for e in restored.executions} == {"agent", "workflow"}
    for execution, call in zip(restored.executions, calls, strict=True):
        assert execution.execution_success
        trace = next(t for t in execution.traces if t.event_type == "llm_response")
        assert trace.llm_metadata.request_timeout_seconds == call["timeout"]
        assert trace.llm_metadata.error is None
        if request_timeout == 40:
            assert call["timeout"] == 40
        else:
            assert 0 < call["timeout"] <= 90 < request_timeout
    raw = json.loads((path / "executions.json").read_bytes())
    assert all(item["state"] == "completed" for item in raw["items"])
    assert "synthetic-key" not in "".join(p.read_text() for p in path.glob("*.json"))
