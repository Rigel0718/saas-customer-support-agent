import json
from unittest.mock import MagicMock

import pytest

from app.diagnosis.llm import call_model
from app.diagnosis.runtime import ExecutionBudget
from app.schemas.support import TraceDTO
from evaluation.dataset import load_bundle, sha256
from evaluation.metrics import execution_metrics, load_pricing
from evaluation.results import ResultStore, prepare_regrade
from scripts import evaluate
from tests.test_agent_loop import final
from tests.test_evaluation_results import make_store
from tests.test_support_models import NOW


@pytest.fixture(autouse=True)
def synthetic_environment(monkeypatch):
    for name in (
        "LLM_REQUEST_TIMEOUT_SECONDS",
        "AGENT_TIMEOUT_SECONDS",
        "WORKFLOW_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "synthetic-model")
    monkeypatch.setenv("OPENAI_API_KEY", "sensitive-marker")
    monkeypatch.setenv("EVALUATION_DATABASE_URL", "postgresql://db.invalid/evaluation")
    monkeypatch.setenv("DATABASE_URL", "postgresql://db.invalid/development")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://db.invalid/test")


@pytest.mark.parametrize(
    "environment,options,expected",
    [
        ({}, {}, (20, 60)),
        (
            {
                "LLM_REQUEST_TIMEOUT_SECONDS": "30",
                "AGENT_TIMEOUT_SECONDS": "80",
                "WORKFLOW_TIMEOUT_SECONDS": "80",
            },
            {},
            (30, 80),
        ),
        (
            {
                "LLM_REQUEST_TIMEOUT_SECONDS": "20",
                "AGENT_TIMEOUT_SECONDS": "60",
                "WORKFLOW_TIMEOUT_SECONDS": "90",
            },
            {"request_timeout": "40", "execution_timeout": "120"},
            (40, 120),
        ),
        (
            {
                "LLM_REQUEST_TIMEOUT_SECONDS": "sensitive-marker",
                "AGENT_TIMEOUT_SECONDS": "NaN",
                "WORKFLOW_TIMEOUT_SECONDS": "",
            },
            {"request_timeout": "40", "execution_timeout": "120"},
            (40, 120),
        ),
    ],
)
def test_timeout_precedence(monkeypatch, environment, options, expected):
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    settings = evaluate.diagnosis_settings(**options)
    assert settings.llm_request_timeout_seconds == expected[0]
    assert (
        settings.agent_timeout_seconds
        == settings.workflow_timeout_seconds
        == expected[1]
    )
    assert (settings.agent_max_steps, settings.agent_max_tool_calls) == (8, 16)


@pytest.mark.parametrize(
    "flag", ["--llm-request-timeout-seconds", "--execution-timeout-seconds"]
)
@pytest.mark.parametrize("value", ["0", "-1", "", "NaN", "inf", "sensitive-marker"])
def test_invalid_cli_timeout_precedes_all_side_effects(
    monkeypatch, tmp_path, capsys, flag, value
):
    def forbidden(*args, **kwargs):
        pytest.fail("Timeout validation must precede side effects")

    monkeypatch.setattr(evaluate, "build_engine", forbidden)
    monkeypatch.setattr(evaluate, "create_run_schema", forbidden)
    monkeypatch.setattr(evaluate, "run_collection", forbidden)
    path = tmp_path / "run"
    assert (
        evaluate.main(
            [
                "run",
                "--allow-live-diagnosis",
                "--output-dir",
                str(path),
                f"{flag}={value}",
            ]
        )
        == 1
    )
    assert not path.exists()
    captured = capsys.readouterr()
    assert captured.out == "평가 중단: invalid_timeout_configuration\n"
    assert captured.err == ""


def test_unequal_environment_budgets_rejected_before_side_effects(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("AGENT_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("WORKFLOW_TIMEOUT_SECONDS", "90")
    build = MagicMock()
    monkeypatch.setattr(evaluate, "build_engine", build)
    path = tmp_path / "run"
    assert (
        evaluate.main(["run", "--allow-live-diagnosis", "--output-dir", str(path)]) == 1
    )
    build.assert_not_called()
    assert not path.exists()
    assert "--execution-timeout-seconds" in capsys.readouterr().out


def test_fake_cli_runner_manifest_and_budget_limited_trace(monkeypatch, tmp_path):
    engine = MagicMock()
    monkeypatch.setattr(evaluate, "build_engine", lambda *a: engine)
    monkeypatch.setattr(evaluate, "verify_database", lambda *a: None)
    monkeypatch.setattr(
        evaluate, "create_run_schema", lambda *a: (engine, "synthetic_schema")
    )
    monkeypatch.setattr(evaluate, "insert_customers", lambda *a: None)
    monkeypatch.setattr(evaluate.FixtureGuard, "capture", lambda *a: object())
    seen = []

    def fake_collection(store, guard, settings):
        for execution in store.executions:
            traces = []
            ticks = [0]
            budget = ExecutionBudget(settings.agent_timeout_seconds, lambda: ticks[0])
            ticks[0] = settings.agent_timeout_seconds - 6

            def fake(**kwargs):
                seen.append((execution.mode, kwargs["timeout"]))
                return final()

            call_model(
                settings, [], "instructions", traces, budget, step=1, create=fake
            )
            execution.traces = traces
        store.manifest.status = "collected"
        store.save_collection()

    monkeypatch.setattr(evaluate, "run_collection", fake_collection)
    path = tmp_path / "run"
    assert (
        evaluate.main(
            [
                "run",
                "--allow-live-diagnosis",
                "--output-dir",
                str(path),
                "--llm-request-timeout-seconds",
                "40",
                "--execution-timeout-seconds",
                "90",
            ]
        )
        == 0
    )
    restored = ResultStore.load(path)
    assert restored.manifest.configuration["llm_request_timeout_seconds"] == 40
    assert restored.manifest.configuration["agent_timeout_seconds"] == 90
    assert restored.manifest.configuration["workflow_timeout_seconds"] == 90
    assert {mode for mode, _ in seen} == {"agent", "workflow"}
    assert all(timeout == 6 for _, timeout in seen)
    assert all(
        e.traces[0].llm_metadata.request_timeout_seconds == 6
        for e in restored.executions
    )
    assert "sensitive-marker" not in "".join(p.read_text() for p in path.glob("*.json"))


def test_legacy_results_and_regrade_preserve_unknown_timeouts_and_source(tmp_path):
    store = make_store(tmp_path / "legacy")
    execution = store.executions[0]
    execution.traces = [
        TraceDTO(
            sequence=1,
            event_type="llm_response",
            created_at=NOW,
            error_code="llm_error",
            duration_ms=20063,
        )
    ]
    execution.metrics = execution_metrics(execution, load_pricing())
    store.initialize()
    manifest_path = store.directory / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["configuration"].pop("llm_request_timeout_seconds")
    manifest_path.write_text(json.dumps(manifest))
    executions_path = store.directory / "executions.json"
    executions = json.loads(executions_path.read_bytes())
    for item in executions["items"]:
        for trace in item["traces"]:
            trace.pop("llm_metadata")
    executions_path.write_text(json.dumps(executions))
    originals = {p.name: p.read_bytes() for p in store.directory.glob("*.json")}

    restored = ResultStore.load(store.directory)
    assert "llm_request_timeout_seconds" not in restored.manifest.configuration
    assert restored.executions[0].traces[0].llm_metadata is None
    assert restored.executions[0].traces[0].error_code == "llm_error"
    assert restored.executions[0].metrics == execution.metrics
    target = prepare_regrade(restored, load_bundle(), load_pricing())
    assert target.executions == restored.executions
    assert "llm_request_timeout_seconds" not in target.manifest.configuration
    assert target.manifest.source["sha256"] == {
        name: sha256(raw) for name, raw in originals.items()
    }
    assert all(
        (store.directory / name).read_bytes() == raw for name, raw in originals.items()
    )
