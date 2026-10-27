import json
import shlex
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from psycopg import OperationalError as DriverOperationalError
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.schemas.support import RequestDTO
from evaluation.dataset import EvaluationError, load_bundle, sha256
from evaluation.judge import Judge, JudgeConfig, grade_store
from evaluation.metrics import load_pricing
from evaluation.results import ResultStore, prepare_regrade
from evaluation.runner import run_collection
from scripts import evaluate
from tests.test_evaluation_fixtures import evaluation_database  # noqa: F401
from tests.test_evaluation_judge import (
    diagnosis,
    fake_calibration,
    judge_response,
    reviewed_calibration,
)
from tests.test_evaluation_results import make_store
from tests.test_evaluation_runner import DiagnosisFake, prepared_store
from tests.test_support_models import NOW


def test_plan_has_no_environment_db_or_external_calls(monkeypatch, capsys):
    def forbidden(*a, **kw):
        pytest.fail("No external or database calls permitted")

    monkeypatch.setattr(evaluate, "build_engine", forbidden)
    monkeypatch.setattr(evaluate, "judge_client", forbidden)
    monkeypatch.setattr(evaluate, "diagnosis_settings", forbidden)
    assert evaluate.main(["plan", "--repetitions", "5"]) == 0
    assert json.loads(capsys.readouterr().out)["execution_count"] == 50


@pytest.mark.parametrize(
    "command", [["run"], ["calibrate"], ["judge", "--run-dir", "absent"]]
)
def test_missing_live_flag_stops_before_any_side_effect(monkeypatch, command):
    def forbidden(*a, **kw):
        pytest.fail("Gate must precede local loading, SDK, and DB")

    monkeypatch.setattr(evaluate, "load_bundle", forbidden)
    monkeypatch.setattr(evaluate, "run_command", forbidden)
    monkeypatch.setattr(evaluate, "judge_client", forbidden)
    assert evaluate.main(command) == 1


def test_diagnosis_flag_dispatch_and_safe_configuration_errors(
    monkeypatch, capsys, caplog, tmp_path
):
    calls = []
    monkeypatch.setattr(
        evaluate, "run_command", lambda *args: calls.append(args) or tmp_path
    )
    assert evaluate.main(["run", "--allow-live-diagnosis"]) == 0
    assert len(calls) == 1
    capsys.readouterr()

    def failure(*args):
        raise RuntimeError("sensitive-configuration-error")

    monkeypatch.setattr(evaluate, "run_command", failure)
    assert evaluate.main(["run", "--allow-live-diagnosis"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "평가 중단: evaluation_io_or_configuration_error\n"
    assert captured.err == ""
    assert "sensitive-configuration-error" not in caplog.text


@pytest.mark.parametrize(
    "failure,expected_code",
    [
        ("invalid_url", "invalid_database_selection"),
        ("engine", "run_environment_error"),
        ("driver_connection", "database_unavailable"),
        ("sqlalchemy_connection", "database_unavailable"),
        ("schema", "schema_preparation_failed"),
    ],
)
def test_database_failures_hide_credentials_in_cli_logs_and_artifacts(
    monkeypatch, capsys, caplog, tmp_path, failure, expected_code
):
    # Synthetic markers only; no .env loading, real DB connection, or SDK calls.
    username, password, api_key = (
        "synthetic-user",
        "synthetic-password",
        "synthetic-key",
    )
    port = "invalid-port" if failure == "invalid_url" else "5432"
    url = f"postgresql+psycopg://{username}:{password}@db.invalid:{port}/evaluation"
    detail = f"connection failed: {url}; password={password}; api_key={api_key}"
    monkeypatch.setenv("EVALUATION_DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_URL", "postgresql://db.invalid/development")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://db.invalid/test")
    monkeypatch.setenv("OPENAI_API_KEY", api_key)
    monkeypatch.setenv("OPENAI_MODEL", "synthetic-model")

    def forbidden(*args, **kwargs):
        pytest.fail("DB preparation failure must precede diagnosis and SDK calls")

    monkeypatch.setattr(evaluate, "run_collection", forbidden)
    monkeypatch.setattr(evaluate, "judge_client", forbidden)
    engine = MagicMock()
    build = MagicMock(return_value=engine)
    monkeypatch.setattr(evaluate, "build_engine", build)
    if failure == "engine":
        build.side_effect = ValueError(detail)
    elif failure == "driver_connection":
        engine.connect.side_effect = DriverOperationalError(detail)
    elif failure == "sqlalchemy_connection":
        engine.connect.side_effect = OperationalError(
            "SELECT current_database()", {"url": url}, DriverOperationalError(detail)
        )
    elif failure == "schema":
        connection = engine.connect.return_value.__enter__.return_value
        connection.scalar.return_value = "evaluation"
        engine.begin.side_effect = ProgrammingError(
            "CREATE SCHEMA", {"url": url}, RuntimeError(detail)
        )

    path = tmp_path / "run"
    assert (
        evaluate.main(["run", "--output-dir", str(path), "--allow-live-diagnosis"]) == 1
    )
    captured = capsys.readouterr()
    assert captured.out == f"평가 중단: {expected_code}\n"
    assert captured.err == ""
    artifacts = ""
    if failure == "invalid_url":
        build.assert_not_called()
        assert not path.exists()
    else:
        store = ResultStore.load(path)
        assert store.manifest.error_code == expected_code
        assert store.manifest.status == "invalid_environment"
        assert not store.summary["valid_comparison"]
        assert all(item.state == "not_started" for item in store.executions)
        artifacts = "".join(file.read_text() for file in path.glob("*.json"))
        if failure != "engine":
            engine.dispose.assert_called_once_with()
    observable = captured.out + captured.err + caplog.text + artifacts
    assert all(value not in observable for value in (url, username, password, api_key))
    assert "Traceback" not in observable


@pytest.mark.parametrize("repetitions", [1, 5])
def test_fake_full_collection_grading_and_independent_regrade(
    tmp_path, evaluation_db, monkeypatch, repetitions
):
    store, guard, settings = prepared_store(tmp_path, evaluation_db, repetitions)
    run_collection(store, guard, settings, create=DiagnosisFake(), now=lambda: NOW)
    raw_before = (store.directory / "executions.json").read_bytes()
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return judge_response()

    judge = Judge(
        JudgeConfig("judge-model"), load_bundle(), load_pricing(), create=fake
    )
    monkeypatch.setattr(evaluate, "judge_client", lambda *args: judge)
    assert (
        evaluate.main(
            ["judge", "--run-dir", str(store.directory), "--allow-live-judge"]
        )
        == 0
    )
    first = ResultStore.load(store.directory)
    assert first.judgments["status"] == "graded"
    assert (
        first.summary["by_mode"]["agent"]["execution_success"]["denominator"]
        == 5 * repetitions
    )
    assert (
        first.summary["by_mode"]["agent"]["correctness"]["numerator"] == 4 * repetitions
    )  # Fake all-correct semantic, C04 outcome mismatch
    assert not first.summary["quality_conclusion_ready"]
    assert (store.directory / "executions.json").read_bytes() == raw_before
    assert len(calls) == 10 * repetitions
    originals = {
        name: path.read_bytes()
        for name in (
            "manifest.json",
            "executions.json",
            "judgments.json",
            "summary.json",
        )
        for path in [store.directory / name]
    }

    def forbidden(*args, **kwargs):
        pytest.fail("Regrading must not contact DB or diagnosis")

    monkeypatch.setattr(evaluate, "build_engine", forbidden)
    monkeypatch.setattr(evaluate, "run_command", forbidden)
    assert (
        evaluate.main(
            [
                "judge",
                "--run-dir",
                str(store.directory),
                "--regrade",
                "--allow-live-judge",
            ]
        )
        == 0
    )
    target_path = next((store.directory / "regrades").iterdir())
    target = ResultStore.load(target_path)
    assert target.executions == first.executions
    assert target.judgment_set_id != first.judgment_set_id
    assert target.manifest.source["sha256"] == {
        name: sha256(raw) for name, raw in originals.items()
    }
    assert len(calls) == 20 * repetitions
    assert all(
        (store.directory / name).read_bytes() == raw for name, raw in originals.items()
    )
    assert (
        evaluate.main(
            ["judge", "--run-dir", str(store.directory), "--allow-live-judge"]
        )
        == 1
    )
    assert len(calls) == 20 * repetitions


def test_regrade_duplicate_rejected_and_judge_failures_preserved(
    tmp_path, evaluation_db
):
    store, guard, settings = prepared_store(tmp_path, evaluation_db)
    run_collection(store, guard, settings, create=DiagnosisFake(), now=lambda: NOW)
    set_id = str(uuid4())
    target = prepare_regrade(store, load_bundle(), load_pricing(), set_id)
    with pytest.raises(EvaluationError):
        prepare_regrade(store, load_bundle(), load_pricing(), set_id)
    judge = Judge(
        JudgeConfig("fake"),
        load_bundle(),
        load_pricing(),
        create=lambda **kwargs: (_ for _ in ()).throw(TimeoutError("secret")),
    )
    grade_store(target, judge)
    restored = ResultStore.load(target.directory)
    assert all(
        j["final_verdict"] == "pending" and j["judge_status"] == "judge_failed"
        for j in restored.judgments["items"]
    )
    assert restored.summary["by_mode"]["agent"]["correctness"]["denominator"] == 0


def test_calibration_cli_does_not_fabricate_labels_and_rejects_overwrite(
    tmp_path, monkeypatch
):
    # This scenario needs unreviewed input regardless of local human reviews.
    calibration = evaluate.load_calibration()
    calibration.examples_reviewed = False
    for example in calibration.examples:
        example.human.label = None
    monkeypatch.setattr(evaluate, "load_calibration", lambda *args: calibration)
    calls = []
    judge = Judge(
        JudgeConfig("fake"),
        load_bundle(),
        load_pricing(),
        create=lambda **kwargs: calls.append(kwargs) or judge_response(),
    )
    monkeypatch.setattr(evaluate, "judge_client", lambda *a: judge)
    path = tmp_path / "calibration.json"
    args = ["calibrate", "--allow-live-judge", "--output", str(path)]
    assert evaluate.main(args) == 0
    result = json.loads(path.read_bytes())
    assert result["status"] == "uncalibrated"
    assert result["agreement"]["denominator"] == 0
    assert len(calls) == 15
    assert evaluate.main(args) == 1
    assert len(calls) == 15


def test_regrade_preserves_scope_and_review_required_for_human_review(
    tmp_path, monkeypatch
):
    # Synthetic collected artifacts; no diagnosis execution or database required.
    store = make_store(tmp_path / "source")
    execution = store.executions[0]
    request_id = uuid4()
    execution.request_id = str(request_id)
    execution.request = RequestDTO(
        id=request_id,
        requester_user_id=uuid4(),
        organization_id=uuid4(),
        content="합성 문의",
        status="completed",
        execution_mode=execution.mode,
        created_at=NOW,
        started_at=NOW,
        finished_at=NOW,
        diagnosis=diagnosis(),
        error_code=None,
    )
    execution.state = "completed"
    execution.started = execution.execution_success = execution.trace_complete = True
    store.manifest.status = "collected"
    store.manifest.completed_count = 1
    store.initialize()
    originals = {p.name: p.read_bytes() for p in store.directory.glob("*.json")}
    _, calibration = fake_calibration(reviewed_calibration())
    calibration_path = tmp_path / "synthetic-calibration.json"
    calibration_path.write_text(json.dumps(calibration))
    calls = []
    judge = Judge(
        JudgeConfig("fake"),
        load_bundle(),
        load_pricing(),
        create=lambda **kwargs: (
            calls.append(kwargs) or judge_response("review_required")
        ),
    )

    def forbidden(*args, **kwargs):
        pytest.fail("Regrade must not execute diagnosis or contact DB")

    monkeypatch.setattr(evaluate, "judge_client", lambda *args: judge)
    monkeypatch.setattr(evaluate, "build_engine", forbidden)
    monkeypatch.setattr(evaluate, "run_command", forbidden)
    assert (
        evaluate.main(
            [
                "judge",
                "--run-dir",
                str(store.directory),
                "--regrade",
                "--calibration-result",
                str(calibration_path),
                "--allow-live-judge",
            ]
        )
        == 0
    )
    target = ResultStore.load(next((store.directory / "regrades").iterdir()))
    assert len(calls) == 1
    judgment = target.judgments["items"][0]
    assert (
        judgment["semantic_verdict"] == judgment["final_verdict"] == "review_required"
    )
    assert judgment["judge_status"] == "judged" and judgment["outcome_match"]
    for artifact in (target.judgments, target.summary):
        applied = artifact["calibration"]
        assert applied["status"] == "calibrated"
        for key in ("criteria_version", "coverage", "limitation"):
            assert applied[key] == calibration[key]
    group = target.summary["by_mode"][execution.mode]
    assert group["verdict_counts"]["review_required"] == 1
    assert group["correctness"]["denominator"] == 0
    assert target.summary["quality_conclusion_ready"]
    assert target.summary["calibration"]["coverage"]["unassessed_verdicts"] == [
        "review_required"
    ]
    assert all(
        (store.directory / name).read_bytes() == raw for name, raw in originals.items()
    )


def test_runbook_cli_examples_parse_and_help(capsys):
    text = Path("docs/evaluation_runbook.md").read_text()
    examples = 0
    for line in text.splitlines():
        if line.startswith("uv run ") and " -m scripts.evaluate " in line:
            tokens = shlex.split(line)
            assert tokens[:6] == ["uv", "run", "--env-file", ".env", "python", "-m"]
            args = tokens[7:]
            examples += 1
            if args == ["--help"]:
                with pytest.raises(SystemExit) as caught:
                    evaluate.parser().parse_args(args)
                assert caught.value.code == 0
            else:
                evaluate.parser().parse_args(args)
    assert examples == 11


def test_invalid_gt_rejected_before_fixture_writes_or_models(tmp_path, monkeypatch):
    bundle = load_bundle()
    gt = bundle.gt.model_dump()
    gt["cases"][4]["primary_causes"] = ["permission_denied"]
    path = tmp_path / "invalid-gt.json"
    path.write_text(json.dumps(gt))

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid GT must stop before DB or LLM")

    monkeypatch.setattr(evaluate, "run_command", forbidden)
    monkeypatch.setattr(evaluate, "judge_client", forbidden)
    assert evaluate.main(["run", "--gt", str(path), "--allow-live-diagnosis"]) == 1
    assert not (tmp_path / "run").exists()


def test_preparation_error_preserves_invalid_manifest_and_summary(
    tmp_path, monkeypatch
):
    from app.config import Settings

    monkeypatch.setattr(
        evaluate,
        "diagnosis_settings",
        lambda **kwargs: Settings("unused", {}, openai_model="fake"),
    )
    monkeypatch.setenv("EVALUATION_DATABASE_URL", "postgresql://host/evaluation")
    monkeypatch.setenv("DATABASE_URL", "postgresql://host/development")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://host/test")
    monkeypatch.setattr(
        evaluate,
        "build_engine",
        lambda url: (_ for _ in ()).throw(RuntimeError("sensitive")),
    )
    path = tmp_path / "run"
    assert (
        evaluate.main(["run", "--output-dir", str(path), "--allow-live-diagnosis"]) == 1
    )
    store = ResultStore.load(path)
    assert store.manifest.status == "invalid_environment"
    assert not store.summary["valid_comparison"]
    assert all(e.state == "not_started" for e in store.executions)
