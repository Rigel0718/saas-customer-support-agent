import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import SupportRequest
from app.services import support
from evaluation.dataset import EvaluationError, load_bundle
from evaluation.fixtures import FixtureGuard, insert_customers, record_id
from evaluation.metrics import load_pricing
from evaluation.results import ResultStore, build_manifest
from evaluation.runner import execution_plan, run_collection
from tests.test_agent_loop import evidence, final, function, response
from tests.test_evaluation_fixtures import evaluation_database  # noqa: F401
from tests.test_support_models import NOW


class DiagnosisFake:
    def __init__(self, outcome="diagnosed"):
        self.calls = []
        self.outcome = outcome

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        serialized = json.dumps(kwargs, ensure_ascii=False)
        for prohibited in (
            "C01",
            "C02",
            "C03",
            "C04",
            "C05",
            "primary_causes",
            "excluded_causes",
            "permission_denied",
        ):
            assert prohibited not in serialized
        assert kwargs["model"] == "shared-test-model"
        payload = json.loads(kwargs["input"][0]["content"])
        if "tools" in kwargs and len(kwargs["input"]) == 1:
            return response(
                [
                    function(
                        "get_user", {"user_id": payload["requester_user_id"]}, "user"
                    ),
                    function("search_policy", {"query": "권한"}, "policy"),
                ]
            )
        refs = (
            [evidence(3), evidence(4, "permissions")]
            if "tools" in kwargs
            else [evidence(2), evidence(7, "permissions")]
        )
        return final(
            self.outcome,
            refs,
            summary="의미 정답과 실행 성공은 독립이다.",
            causes=["임의 원인"] if self.outcome == "diagnosed" else [],
        ) | {"usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}}


def prepared_store(tmp_path, evaluation_db, repetitions=1):
    engine, run_id = evaluation_db
    bundle = load_bundle()
    insert_customers(engine, bundle, run_id, NOW)
    guard = FixtureGuard.capture(engine, bundle, run_id, NOW, now=lambda: NOW)
    settings = Settings("not-serialized", {}, openai_model="shared-test-model")
    plan = execution_plan(run_id, repetitions)
    store = ResultStore(
        tmp_path / "run",
        build_manifest(
            run_id, NOW, repetitions, plan, bundle, settings, load_pricing()
        ),
        plan,
    )
    store.initialize()
    return store, guard, settings


@pytest.mark.parametrize("repetitions,total", [(1, 10), (5, 50)])
def test_order_new_requests_shared_fixture_real_services_fake_responses(
    tmp_path, evaluation_db, repetitions, total
):
    store, guard, settings = prepared_store(tmp_path, evaluation_db, repetitions)
    fake = DiagnosisFake()
    run_collection(store, guard, settings, create=fake, now=lambda: NOW)
    assert store.manifest.status == "collected"
    assert len(store.executions) == total
    assert len({e.request_id for e in store.executions}) == total
    assert all(e.execution_success and e.trace_complete for e in store.executions)
    assert [(e.repeat, e.case_id, e.mode) for e in store.executions] == [
        (r, f"C{i:02}", mode)
        for r in range(1, repetitions + 1)
        for i in range(1, 6)
        for mode in (("agent", "workflow") if r % 2 else ("workflow", "agent"))
    ]
    for e in store.executions:
        assert e.request.requester_user_id == record_id(
            guard.run_id, e.case_id, "requester"
        )
        assert e.request.organization_id == record_id(
            guard.run_id, e.case_id, "organization"
        )
        assert e.metrics["tool_calls"]["value"] == (2 if e.mode == "agent" else 7)
    with Session(guard.engine) as session:
        assert session.scalar(select(func.count()).select_from(SupportRequest)) == total
    restored = ResultStore.load(store.directory)
    assert restored.executions == store.executions
    assert (
        restored.executions[0].request.diagnosis.evidence
        == store.executions[0].request.diagnosis.evidence
    )
    with pytest.raises(EvaluationError, match="collection_resume_not_supported"):
        run_collection(store, guard, settings, create=fake, now=lambda: NOW)


@pytest.mark.parametrize("repetitions", [0, 2, 10, True])
def test_invalid_plan_rejected(repetitions):
    with pytest.raises(EvaluationError, match="invalid_repetitions"):
        execution_plan(uuid4(), repetitions)


@pytest.mark.parametrize("outcome", ["needs_information", "escalate"])
def test_all_valid_outcomes_are_execution_success(tmp_path, evaluation_db, outcome):
    store, guard, settings = prepared_store(tmp_path, evaluation_db)
    run_collection(
        store, guard, settings, create=DiagnosisFake(outcome), now=lambda: NOW
    )
    assert all(e.execution_success for e in store.executions)


def test_handled_failure_continues_new_requests(tmp_path, evaluation_db):
    store, guard, settings = prepared_store(tmp_path, evaluation_db)
    good = DiagnosisFake()
    calls = []

    def fake(**kw):
        calls.append(kw)
        return response([]) if len(calls) == 1 else good(**kw)

    run_collection(store, guard, settings, create=fake, now=lambda: NOW)
    first = store.executions[0]
    assert first.state == "failed" and not first.execution_success
    assert first.trace_complete and first.metrics["llm_calls"]["value"] == 1
    assert store.executions[1].execution_success
    assert len(store.executions) == 10 and store.manifest.status == "collected"


@pytest.mark.parametrize(
    "failure", ["lost_commit", "trace", "running", "interrupt", "create", "preflight"]
)
def test_failure_domains_recovery_and_censoring(
    tmp_path, evaluation_db, monkeypatch, failure
):
    store, guard, settings = prepared_store(tmp_path, evaluation_db)
    diagnose = support.diagnose_request
    fake = DiagnosisFake()
    calls = []

    def replacement(session, context, request_id, *args, **kwargs):
        calls.append(request_id)
        if failure == "running":
            session.execute(
                update(SupportRequest)
                .where(SupportRequest.id == request_id)
                .values(status="running", execution_mode=kwargs["mode"], started_at=NOW)
            )
            session.commit()
            return support.request_dto(
                session, support.get_request(session, context, request_id)
            )
        if failure == "interrupt":
            raise KeyboardInterrupt
        result = diagnose(session, context, request_id, *args, **kwargs)
        if failure == "lost_commit":
            raise SQLAlchemyError("sensitive error text is never exported")
        return result

    monkeypatch.setattr(support, "diagnose_request", replacement)
    if failure == "trace":
        monkeypatch.setattr(
            support,
            "get_traces",
            lambda *a: (_ for _ in ()).throw(RuntimeError("sensitive")),
        )
    if failure == "create":
        monkeypatch.setattr(
            support,
            "create_request",
            lambda *a: (_ for _ in ()).throw(SQLAlchemyError("sensitive")),
        )
    if failure == "preflight":
        monkeypatch.setattr(
            guard,
            "check",
            lambda **kw: (_ for _ in ()).throw(EvaluationError("fixture_changed")),
        )
    run_collection(store, guard, settings, create=fake, now=lambda: NOW)
    first = store.executions[0]
    if failure == "lost_commit":
        assert first.execution_success and first.state == "completed"
        assert len(calls) == 10  # one diagnose call per new request, never retry
    elif failure == "trace":
        assert first.execution_success and first.metrics["llm_calls"]["value"] is None
        assert first.errors[0]["domain"] == "collection"
    elif failure in ("running", "interrupt"):
        assert (
            first.state == "incomplete"
            and first.censored
            and not first.execution_success
        )
        assert first.metrics["latency_ms"]["value"] is None
        if failure == "interrupt":
            assert store.manifest.status == "interrupted" and len(calls) == 1
    elif failure == "create":
        assert first.state == "environment_error" and not first.started
        assert first.execution_success is None and len(calls) == 0
        assert store.manifest.status == "invalid_environment"
    else:
        assert first.state == "not_started" and len(calls) == 0
        assert store.manifest.status == "invalid_environment"
    assert "sensitive" not in (store.directory / "executions.json").read_text()


def test_latency_boundary_excludes_auth_collection_and_files(
    tmp_path, evaluation_db, monkeypatch
):
    from app.services import customer

    store, guard, settings = prepared_store(tmp_path, evaluation_db)
    time = [0.0]

    def delayed(original, delay):
        def call(*a, **kw):
            time[0] += delay
            return original(*a, **kw)

        return call

    monkeypatch.setattr(customer, "authenticate", delayed(customer.authenticate, 100))
    monkeypatch.setattr(support, "create_request", delayed(support.create_request, 2))
    monkeypatch.setattr(
        support, "diagnose_request", delayed(support.diagnose_request, 3)
    )
    monkeypatch.setattr(support, "get_traces", delayed(support.get_traces, 100))
    monkeypatch.setattr(store, "save_collection", delayed(store.save_collection, 100))
    run_collection(
        store,
        guard,
        settings,
        create=DiagnosisFake(),
        clock=lambda: time[0],
        now=lambda: NOW,
    )
    assert all(e.elapsed_ms == 5000 for e in store.executions)
    assert sum(t.duration_ms or 0 for t in store.executions[0].traces) < 5000


def test_file_failure_stops_new_model_calls(tmp_path, evaluation_db, monkeypatch):
    store, guard, settings = prepared_store(tmp_path, evaluation_db)
    saved = store.save_collection
    count = [0]

    def failing():
        count[0] += 1
        if count[0] >= 3:
            raise EvaluationError("result_storage_failed")
        saved()

    monkeypatch.setattr(store, "save_collection", failing)
    fake = DiagnosisFake()
    with pytest.raises(EvaluationError, match="result_storage_failed"):
        run_collection(store, guard, settings, create=fake, now=lambda: NOW)
    restored = ResultStore.load(store.directory)
    assert restored.executions[0].execution_success
    assert all(e.state == "not_started" for e in restored.executions[1:])
    assert len(fake.calls) == 3  # first agent two calls and second workflow one


def test_postflight_mutation_stops_following_diagnoses(tmp_path, evaluation_db):
    from app.models import User

    store, guard, settings = prepared_store(tmp_path, evaluation_db)
    good = DiagnosisFake()
    mutated = [False]

    def fake(**kwargs):
        result = good(**kwargs)
        if not mutated[0]:
            with Session(guard.engine) as session:
                session.add(
                    User(
                        id=uuid4(),
                        organization_id=record_id(guard.run_id, "C01", "organization"),
                        role="member",
                    )
                )
                session.commit()
            mutated[0] = True
        return result

    run_collection(store, guard, settings, create=fake, now=lambda: NOW)
    assert store.manifest.status == "invalid_environment"
    assert store.manifest.error_code == "fixture_changed"
    assert store.executions[0].started and all(
        not e.started for e in store.executions[1:]
    )
    assert not store.summary["valid_comparison"]
    assert ResultStore.load(store.directory).executions[0].request is not None


def test_persistent_database_failure_retains_incomplete_and_stops(
    tmp_path, evaluation_db, monkeypatch
):
    store, guard, settings = prepared_store(tmp_path, evaluation_db)
    original_check = guard.check
    outage = [False]

    def diagnose(*args, **kwargs):
        outage[0] = True
        raise SQLAlchemyError("never-export-database-detail")

    def sessions():
        if outage[0]:
            raise SQLAlchemyError("never-export-database-detail")
        return Session(guard.engine)

    def check(**kwargs):
        if outage[0]:
            raise EvaluationError("fixture_validation_failed")
        original_check(**kwargs)

    monkeypatch.setattr(support, "diagnose_request", diagnose)
    monkeypatch.setattr(guard, "check", check)
    fake = DiagnosisFake()
    run_collection(
        store, guard, settings, create=fake, session_factory=sessions, now=lambda: NOW
    )
    assert store.manifest.status == "invalid_environment"
    assert store.executions[0].state == "incomplete" and store.executions[0].censored
    assert all(not e.started for e in store.executions[1:]) and not fake.calls
    assert {error["domain"] for error in store.executions[0].errors} == {
        "execution",
        "collection",
    }


def test_invalid_evidence_is_still_rejected_by_shared_service(tmp_path, evaluation_db):
    store, guard, settings = prepared_store(tmp_path, evaluation_db)

    def fake(**kwargs):
        return final("diagnosed", [evidence(999, "permissions")])

    run_collection(store, guard, settings, create=fake, now=lambda: NOW)
    assert all(
        e.state == "failed" and e.request.error_code == "invalid_output"
        for e in store.executions
    )
    assert all(e.request.diagnosis is None for e in store.executions)
    assert all(e.trace_complete and e.elapsed_ms >= 0 for e in store.executions)
