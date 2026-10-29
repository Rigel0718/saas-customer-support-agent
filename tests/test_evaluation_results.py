import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import Settings
from evaluation.dataset import EvaluationError, load_bundle
from evaluation.metrics import load_pricing
from evaluation.results import ResultStore, build_manifest, prepare_regrade
from app.schemas.support import FailureMetadata, RequestDTO, TraceDTO
from app.seed import uid
from evaluation.runner import execution_plan
from tests.test_support_models import NOW


def make_store(directory):
    run_id = uuid4()
    plan = execution_plan(run_id, 1)
    manifest = build_manifest(
        run_id,
        NOW,
        1,
        plan,
        load_bundle(),
        Settings(
            "never-store-database-secret",
            {"never-store-token": uuid4()},
            openai_api_key="never-store-api-key",
            openai_model="fake-model",
        ),
        load_pricing(),
    )
    return ResultStore(directory, manifest, plan)


def test_four_artifacts_roundtrip_and_allowlisted_provenance(tmp_path):
    store = make_store(tmp_path / "run")
    store.initialize()
    assert len(list(store.directory.glob("*.json"))) == 4
    restored = ResultStore.load(store.directory)
    assert restored.manifest == store.manifest
    assert restored.executions == store.executions
    manifest = restored.manifest
    assert manifest.provenance["code"]["revision"]
    assert manifest.provenance["code"]["files_sha256"]["app/agent/loop.py"]
    assert manifest.provenance["gt"]["snapshot"]["gt_version"]
    assert manifest.configuration["diagnosis_model"] == "fake-model"
    for file in store.directory.glob("*.json"):
        assert "never-store" not in file.read_text()
    with pytest.raises(EvaluationError):
        store.initialize()


def test_atomic_error_leaves_previous_file_and_completed_executions(
    tmp_path, monkeypatch
):
    from evaluation import results

    store = make_store(tmp_path / "run")
    store.initialize()
    previous = (store.directory / "executions.json").read_bytes()
    original = results.os.replace

    def fail(source, target):
        if Path(target).name == "executions.json":
            raise OSError("never-store-error-secret")
        original(source, target)

    monkeypatch.setattr(results.os, "replace", fail)
    store.executions[0].state = "failed"
    with pytest.raises(EvaluationError, match="result_storage_failed"):
        store.save_collection()
    assert (store.directory / "executions.json").read_bytes() == previous
    assert not list(store.directory.glob(".pending-*"))


@pytest.mark.parametrize("bad", ["schema_version", "run_id", "execution_id", "set_id"])
def test_artifact_link_validation(tmp_path, bad):
    store = make_store(tmp_path / "run")
    store.initialize()
    file = store.directory / "executions.json"
    data = json.loads(file.read_text())
    if bad == "execution_id":
        data["items"][0]["execution_id"] = "bad"
    elif bad == "set_id":
        data["judgment_set_id"] = "bad"
    else:
        data[bad] = "bad"
    file.write_text(json.dumps(data))
    with pytest.raises(EvaluationError, match="invalid_result_artifacts"):
        ResultStore.load(store.directory)


@pytest.mark.parametrize("legacy", [False, True])
def test_failure_metadata_roundtrip_preserves_source(tmp_path, monkeypatch, legacy):
    from app.diagnosis import llm
    from app.services import support

    def forbidden(*args, **kwargs):
        pytest.fail("Diagnosis or LLM calls are forbidden during result loading/regrade preparation")

    monkeypatch.setattr(llm, "responses_create", forbidden)
    monkeypatch.setattr(support, "diagnose_request", forbidden)
    store = make_store(tmp_path / "source")
    execution = store.executions[0]
    request_id = uuid4()
    execution.request_id = str(request_id)
    execution.state = "failed"
    execution.started = True
    execution.execution_success = False
    execution.elapsed_ms = 25
    execution.request = RequestDTO(
        id=request_id, requester_user_id=uid(0x101), organization_id=uid(1),
        content="문의", status="failed", execution_mode=execution.mode,
        created_at=NOW, started_at=NOW, finished_at=NOW, diagnosis=None,
        error_code="invalid_output",
    )
    metadata = FailureMetadata(
        failure_stage="diagnosis_schema", failure_reason="validation_error",
        field_path="summary",
    )
    execution.traces = [
        TraceDTO(sequence=1, event_type="execution_started", created_at=NOW),
        TraceDTO(sequence=2, event_type="final_result", created_at=NOW,
                 error_code="invalid_output", failure_metadata=metadata),
        TraceDTO(sequence=3, event_type="execution_failed", created_at=NOW,
                 error_code="invalid_output"),
    ]
    execution.metrics = {"latency_ms": {"value": 25, "observed": 25, "complete": True}}
    store.initialize()
    if legacy:
        file = store.directory / "executions.json"
        data = json.loads(file.read_text())
        for item in data["items"]:
            for trace in item["traces"]:
                trace.pop("failure_metadata", None)
        file.write_text(json.dumps(data))
    before = {p.name: p.read_bytes() for p in store.directory.glob("*.json")}
    restored = ResultStore.load(store.directory)
    actual = restored.executions[0]
    assert actual.traces[1].failure_metadata == (None if legacy else metadata)
    assert actual.metrics == execution.metrics
    assert actual.request == execution.request
    target = prepare_regrade(restored, load_bundle(), load_pricing())
    assert target.executions == restored.executions
    assert ResultStore.load(target.directory).executions == restored.executions
    from evaluation.dataset import sha256

    assert target.manifest.source["sha256"] == {
        name: sha256(value) for name, value in before.items()
    }
    assert {p.name: p.read_bytes() for p in store.directory.glob("*.json")} == before
