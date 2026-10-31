import json
from collections import Counter
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.config import Settings
from app.models import AuditEvent, Payment, Subscription, User
from evaluation import access_pilot
from evaluation.dataset import EvaluationError
from evaluation.fixtures import create_run_schema, record_id
from evaluation.metrics import load_pricing
from scripts import access_pilot as cli
from tests.test_agent_loop import evidence, final, function, response
from tests.test_support_models import NOW

CONFIG = Settings("unused", {}, openai_model="scripted-model")
WINDOW = timedelta(seconds=access_pilot.NOISE_WINDOW_SECONDS)


def noise(seed=7, count=500, case_id="A01", run_id=None):
    run_id = run_id or record_id(uuid4(), "fixed", "run")
    organization_id = record_id(run_id, "fixed", "organization")
    users = access_pilot.user_ids(run_id, case_id, count)
    events, roles = access_pilot.noise_events(
        seed=seed,
        count=count,
        case_id=case_id,
        organization_id=organization_id,
        users=users,
        reference_time=NOW,
    )
    return events, roles, users


def signature(events):
    return [
        (e.id, e.occurred_at, e.event_type, e.actor_user_id, e.target_user_id, e.payload)
        for e in events
    ]


def test_noise_is_deterministic_per_seed():
    run_id = uuid4()
    first, _, _ = noise(run_id=run_id)
    again, _, _ = noise(run_id=run_id)
    other, _, _ = noise(seed=8, run_id=run_id)
    assert signature(first) == signature(again)
    assert signature(first) != signature(other)


def test_noise_avoids_requester_and_subscription_and_keeps_roles_consistent():
    events, roles, users = noise(count=5000)
    assert len(events) == 5000
    kinds = Counter(e.event_type for e in events)
    assert kinds["subscription_status_changed"] == 0
    assert kinds["role_changed"] > 0
    assert all(e.target_user_id != users["requester"] for e in events)
    assert all(
        NOW - e.occurred_at <= WINDOW and e.occurred_at < NOW
        for e in events
    )
    current = {member: "member" for member in roles}
    for event in sorted(
        (e for e in events if e.event_type == "role_changed"),
        key=lambda e: (e.occurred_at, e.id),
    ):
        assert event.payload["from_role"] == current[event.target_user_id]
        current[event.target_user_id] = event.payload["to_role"]
    assert current == roles


@pytest.mark.parametrize("case_id", list(access_pilot.CASES))
def test_case_state_matches_cause_events(case_id):
    run_id = uuid4()
    rows = access_pilot.case_rows(run_id, case_id, 5000, 7, NOW)
    case = access_pilot.CASES[case_id]
    users = access_pilot.user_ids(run_id, case_id, 5000)
    requester = next(r for r in rows if isinstance(r, User) and r.id == users["requester"])
    subscription = next(r for r in rows if isinstance(r, Subscription))
    payments = [r for r in rows if isinstance(r, Payment)]
    # 노이즈는 요청자를 대상으로 하거나 구독을 바꾸지 않으므로 나머지가 원인 이벤트다.
    causes = [
        r
        for r in rows
        if isinstance(r, AuditEvent)
        and (
            r.target_user_id == users["requester"]
            or r.event_type == "subscription_status_changed"
        )
    ]
    assert requester.role == case.requester_role
    assert subscription.status == case.subscription_status
    assert any(p.status == "failed" for p in payments) == case.suspended
    demotions = [
        c for c in causes if c.payload.get("to_role") == "member"
    ]
    suspensions = [c for c in causes if c.event_type == "subscription_status_changed"]
    promotions = [c for c in causes if c.payload.get("to_role") == "admin"]
    assert len(demotions) == case.demoted and len(suspensions) == case.suspended
    assert len(promotions) == case.old_promotion
    for demotion in demotions:
        assert demotion.actor_user_id == users["admin_b"]
        assert demotion.payload == {"from_role": "admin", "to_role": "member"}
    for suspension in suspensions:
        assert suspension.actor_user_id is None
        assert suspension.payload["to_status"] == subscription.status
    for promotion in promotions:
        assert (NOW - promotion.occurred_at).days == 5
    assert sum(isinstance(r, AuditEvent) for r in rows) == 5000 + len(causes)


def test_execution_plan_alternates_modes():
    plan = access_pilot.execution_plan(["A01", "A02"], [50, 5000], 2)
    assert len(plan) == 16
    assert [p["order"] for p in plan] == list(range(1, 17))
    first, second = plan[:8], plan[8:]
    assert [p["mode"] for p in first[:2]] == ["agent", "workflow"]
    assert [p["mode"] for p in second[:2]] == ["workflow", "agent"]
    assert {(p["noise"], p["case_id"]) for p in first} == {
        (50, "A01"),
        (50, "A02"),
        (5000, "A01"),
        (5000, "A02"),
    }


class FakeResponses:
    """Agent는 Audit Event를 한 번 조회한 뒤, Workflow는 즉시 최종 진단을 반환한다."""

    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if "tools" not in kwargs:
            return final("diagnosed", [evidence(2)])
        if any(item.get("type") == "function_call_output" for item in kwargs["input"]):
            return final("diagnosed", [evidence(3)])
        arguments = {
            "start_at": None,
            "end_at": None,
            "actor_user_id": None,
            "target_user_id": None,
            "event_type": "role_changed",
            "limit": 50,
        }
        return response([function("get_audit_events", arguments, call_id="call-a")])


@pytest.fixture
def pilot_db(engine):
    run_id = uuid4()
    db, schema = create_run_schema(
        engine, engine.url.render_as_string(hide_password=False), run_id
    )
    try:
        yield db, run_id
    finally:
        db.dispose()
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def test_collect_with_fake_client(pilot_db):
    db, run_id = pilot_db
    cases, levels = ["A01", "A04"], [50, 1500]
    access_pilot.insert_fixtures(db, run_id, cases, levels, 7, NOW)
    plan = access_pilot.execution_plan(cases, levels, 1)
    fake = FakeResponses()
    checkpoints = []
    results = access_pilot.collect(
        db,
        run_id,
        plan,
        CONFIG,
        load_pricing(),
        create=fake,
        now=lambda: NOW,
        on_record=lambda done: checkpoints.append(len(done)),
    )
    assert len(results) == 8
    assert checkpoints == list(range(1, 9))
    assert len(fake.calls) == 4 * 2 + 4
    for result in results:
        assert result["error"] is None
        assert result["request"]["status"] == "completed"
        assert result["request"]["execution_mode"] == result["mode"]
        assert result["traces"][-1]["event_type"] == "execution_completed"
        assert result["metrics"]["llm_calls"]["value"] == (
            2 if result["mode"] == "agent" else 1
        )
        json.dumps(result)
    workflow = {
        (r["case_id"], r["noise"]): r["audit_prefetch"]
        for r in results
        if r["mode"] == "workflow"
    }
    assert workflow[("A01", 1500)]["returned"] == 500
    assert workflow[("A01", 1500)]["truncated"] is True
    assert workflow[("A04", 50)]["truncated"] is False
    agent = [r for r in results if r["mode"] == "agent"]
    assert all(r["audit_prefetch"] is None for r in agent)
    audit = next(
        t for t in agent[0]["traces"] if t["tool_name"] == "get_audit_events"
    )
    assert audit["observation"]["ok"] is True
    assert all(r["metrics"]["latency_ms"]["value"] is not None for r in results)

    summary = access_pilot.summarize(results)
    assert summary["grading"] == "not_graded"
    assert summary["by_mode"]["agent"]["completed"] == 4
    assert summary["by_mode"]["workflow"]["metrics"]["llm_calls"]["mean"] == 1
    assert set(summary["by_mode_noise"]) == {
        "agent/n50", "agent/n1500", "workflow/n50", "workflow/n1500"
    }
    assert summary["by_case_mode_noise"]["A01/agent/n50"]["outcome_counts"] == {
        "diagnosed": 1
    }
    json.dumps(summary)


def test_save_run_writes_three_files(tmp_path):
    plan = access_pilot.execution_plan(["A01"], [50], 1)
    manifest = access_pilot.build_manifest(
        run_id=uuid4(),
        run_name="access-2-v1",
        reference_time=NOW,
        db_schema="eval_x",
        seed=7,
        noise_levels=[50],
        case_ids=["A01"],
        repetitions=1,
        plan=plan,
        settings=CONFIG,
        pricing=load_pricing(),
    )
    access_pilot.save_run(tmp_path, manifest, [])
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "executions.json",
        "manifest.json",
        "summary.json",
    ]
    stored = json.loads((tmp_path / "manifest.json").read_text())
    assert stored["configuration"]["llm_request_timeout_seconds"] == 20
    assert stored["configuration"]["workflow_audit_limit"] == 500
    assert stored["plan"] == plan
    assert "openai_api_key" not in json.dumps(stored)
    assert stored["provenance"]["gt"]["snapshot"]["gt_version"] == "access-gt-v1"
    executions = json.loads((tmp_path / "executions.json").read_text())
    assert executions["items"] == [] and executions["run_id"] == stored["run_id"]


def test_next_run_name_follows_versioning(tmp_path):
    assert access_pilot.next_run_name(tmp_path, 16) == "access-16-v1"
    for name in ("access-16-v1", "access-16-v3", "access-48-v1", "access-16-vx"):
        (tmp_path / name).mkdir()
    assert access_pilot.next_run_name(tmp_path, 16) == "access-16-v4"
    assert access_pilot.next_run_name(tmp_path, 48) == "access-48-v2"
    assert access_pilot.next_run_name(tmp_path / "missing", 8) == "access-8-v1"


def test_cli_requires_live_flag_before_environment(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("must not run without --allow-live-diagnosis")

    monkeypatch.setattr(cli, "run", forbidden)
    assert cli.main([]) == 2
    assert "live_diagnosis_requires_allow_live_diagnosis" in capsys.readouterr().out


def test_cli_rejects_shared_database_before_schema(monkeypatch, tmp_path):
    monkeypatch.setenv("EVALUATION_DATABASE_URL", "postgresql://host/development")
    monkeypatch.setenv("DATABASE_URL", "postgresql://host/development")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://host/test")

    def forbidden(*args, **kwargs):
        pytest.fail("schema must not be created")

    monkeypatch.setattr(cli, "create_run_schema", forbidden)
    args = cli.parser().parse_args(["--output-dir", str(tmp_path / "run")])
    with pytest.raises(EvaluationError) as exc:
        cli.run(args, settings=CONFIG)
    assert exc.value.code == "invalid_database_selection"
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize(
    "argv,code",
    [
        (["--cases", "A09"], "invalid_cases"),
        (["--noise", "-1"], "invalid_noise"),
        (["--repetitions", "0"], "invalid_repetitions"),
    ],
)
def test_cli_rejects_invalid_arguments(argv, code):
    with pytest.raises(EvaluationError) as exc:
        cli.validate_args(cli.parser().parse_args(argv))
    assert exc.value.code == code


def test_ground_truth_matches_fixture_cases():
    gt = access_pilot.load_ground_truth()
    assert gt.gt_version == "access-gt-v1" and gt.labeling_rules
    for truth in gt.cases:
        case = access_pilot.CASES[truth.case_id]
        expected = set()
        if case.demoted:
            expected.add("role_demoted")
        if case.suspended:
            expected.add("subscription_past_due")
        assert set(truth.primary_causes) == expected
        if case.old_promotion:
            assert "old_role_change" in truth.excluded_causes
        assert "other_user_role_change" in truth.excluded_causes
    run_id = uuid4()
    rows = access_pilot.case_rows(run_id, "A01", 0, 7, NOW)
    users = [r for r in rows if isinstance(r, User)]
    organization = rows[0]
    # required_facts의 인원·한도 수치가 Fixture와 일치한다.
    assert len(users) == 23 and organization.member_limit == 28


def bad_gt(tmp_path, change):
    data = json.loads(access_pilot.GT_PATH.read_text())
    change(data)
    path = tmp_path / "gt.json"
    path.write_text(json.dumps(data, ensure_ascii=False))
    return path


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["cases"][0]["excluded_causes"].append("role_demoted"),
        lambda d: d["cases"][0]["primary_causes"].append("unknown_cause"),
        lambda d: d["cases"][3].update(expected_outcome="diagnosed"),
        lambda d: d["cases"].pop(),
        lambda d: d["cases"][0]["policy_document_ids"].append("missing"),
        lambda d: d.update(labeling_rules=[]),
    ],
)
def test_ground_truth_rejects_inconsistent_files(tmp_path, change):
    with pytest.raises(EvaluationError) as exc:
        access_pilot.load_ground_truth(bad_gt(tmp_path, change))
    assert exc.value.code == "invalid_ground_truth"
