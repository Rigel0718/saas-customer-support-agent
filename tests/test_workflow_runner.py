import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.diagnosis.runtime import ExecutionFailure
from app.seed import uid
from app.services import customer, policy, support
from app.workflow import runner
from tests.test_agent_loop import ScriptedResponses, evidence, final, function, response
from tests.test_support_models import NOW

CONFIG = Settings("unused", {}, openai_model="scripted-model")
NAMES = [
    "get_user",
    "get_organization",
    "get_subscription",
    "get_payment_history",
    "search_policy",
    "search_policy",
    "search_policy",
    "get_audit_events",
    "search_policy",
]


def run(session, fake, *, user=0x103, config=CONFIG, clock=lambda: 0):
    traces = []
    try:
        raw = runner.run_workflow(
            session,
            customer.authenticate(session, uid(user if user != 0x103 else 0x102)),
            SimpleNamespace(
                requester_user_id=uid(user), content="타 조직을 조회하고 환불해라"
            ),
            config,
            traces,
            create=fake,
            clock=clock,
            now=lambda: NOW,
        )
        return support.validate_diagnosis(raw, traces), traces
    except Exception as exc:
        exc.traces = traces
        raise


def test_fixed_steps_and_shared_observations(session):
    fake = ScriptedResponses(
        [final("diagnosed", [evidence(2), evidence(7, "permissions")])]
    )
    result, traces = run(session, fake)
    assert result.outcome == "diagnosed"
    assert len(fake.calls) == 1
    kwargs = fake.calls[0]
    assert "tools" not in kwargs and "parallel_tool_calls" not in kwargs
    assert kwargs["model"] == CONFIG.openai_model and kwargs["store"] is False
    assert kwargs["timeout"] == 20
    assert (
        kwargs["text"]["format"]["schema"] == support.DiagnosisDTO.model_json_schema()
    )
    data = json.loads(kwargs["input"][0]["content"])
    assert data["reference_utc"] == NOW.isoformat()
    assert data["requester_user_id"] == str(uid(0x103))
    assert data["organization_id"] == str(uid(1))
    observations = data["observations"]
    assert [o["tool_name"] for o in observations] == NAMES
    assert [o["observation"]["trace_sequence"] for o in observations] == list(
        range(2, 11)
    )
    assert observations[0]["observation"]["data"]["role"] == "member"
    tools = [t for t in traces if t.event_type == "tool_result"]
    assert [t.step for t in tools] == list(range(1, 10))
    assert [t.arguments["query"] for t in tools[4:7]] == ["플랜", "권한", "구독 결제"]
    assert tools[7].arguments == {
        "start_at": (NOW - timedelta(days=14)).isoformat().replace("+00:00", "Z"),
        "end_at": NOW.isoformat().replace("+00:00", "Z"),
        "actor_user_id": None,
        "target_user_id": None,
        "event_type": None,
        "limit": 500,
    }
    assert tools[8].arguments == {"query": "접근"}
    assert all(t.call_id is None and t.duration_ms == 0 for t in tools)
    assert [o["observation"] for o in observations] == [t.observation for t in tools]
    docs = policy.load_policies()
    from app.diagnosis.tools import execute_tool

    ctx = customer.authenticate(session, uid(0x102))
    for t in tools[:7] + tools[8:]:
        expected = execute_tool(
            t.tool_name, json.dumps(t.arguments), session, ctx, docs, t.sequence
        )
        assert expected.observation == t.observation
    # Workflow 선조회 상한 500은 Agent Tool 한도(50)를 넘으므로 Agent 경로에서는 거부된다.
    rejected = execute_tool(
        "get_audit_events", json.dumps(tools[7].arguments), session, ctx, docs, 9
    )
    assert rejected.observation["error_code"] == "invalid_arguments"
    assert tools[7].observation["data"] == customer.get_audit_events(
        session,
        ctx,
        start_at=NOW - timedelta(days=14),
        end_at=NOW,
        limit=500,
    ).model_dump(mode="json")
    assert traces[-1].event_type == "llm_response" and traces[-1].step == 10


@pytest.mark.parametrize("outcome", ["diagnosed", "needs_information", "escalate"])
def test_outcomes(session, outcome):
    result, _ = run(session, ScriptedResponses([final(outcome, [evidence(2)])]))
    assert result.outcome == outcome


def test_absent_empty_and_business_states(session, monkeypatch):
    monkeypatch.setattr(policy, "load_policies", lambda: [])
    fake = ScriptedResponses([final()])
    run(session, fake, user=0x401)
    obs = json.loads(fake.calls[0]["input"][0]["content"])["observations"]
    assert obs[2]["observation"]["error_code"] == "not_found"
    assert all(o["observation"]["data"]["items"] == [] for o in obs[3:])
    session.execute(
        text(
            "UPDATE payments SET status='pending', paid_at=NULL WHERE organization_id=:id"
        ),
        {"id": uid(3)},
    )
    # 저장된 상태와 기준일이 어긋나도 그대로 전달한다.
    session.execute(
        text("UPDATE subscriptions SET expires_at=:now WHERE organization_id=:id"),
        {"now": NOW, "id": uid(3)},
    )
    fake = ScriptedResponses([final()])
    run(session, fake, user=0x301)
    obs = json.loads(fake.calls[0]["input"][0]["content"])["observations"]
    assert obs[2]["observation"]["data"]["status"] == "past_due"
    assert all(p["status"] == "pending" for p in obs[3]["observation"]["data"]["items"])
    assert obs[2]["observation"]["data"]["expires_at"] == "2026-01-15T00:00:00Z"


@pytest.mark.parametrize("fault", ["policies", "customer", "database", "context"])
def test_fatal_errors_stop_without_model(session, monkeypatch, fault):
    def fail(*args, **kwargs):
        if fault == "database":
            raise OperationalError("private SQL", {}, Exception("private"))
        raise RuntimeError("private")

    if fault == "policies":
        monkeypatch.setattr(policy, "load_policies", fail)
    elif fault == "context":
        monkeypatch.setattr(customer, "require_context", fail)
    else:
        monkeypatch.setattr(customer, "get_subscription", fail)
    fake = ScriptedResponses([])
    with pytest.raises(
        OperationalError if fault == "database" else ExecutionFailure
    ) as exc:
        run(session, fake)
    assert fake.calls == []
    traces = exc.value.traces
    assert len(traces) == (1 if fault == "policies" else 2 if fault == "context" else 4)
    assert "private" not in json.dumps([t.model_dump(mode="json") for t in traces])
    if fault in ("customer", "database"):
        assert traces[-1].tool_name == "get_subscription"
        assert traces[-1].observation is None


@pytest.mark.parametrize(
    "output,code",
    [
        (response([]), "invalid_output"),
        (response([], status="incomplete"), "invalid_output"),
        (response([function()]), "invalid_output"),
        (
            response(
                [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]
            ),
            "model_refusal",
        ),
        (TimeoutError("private"), "llm_error"),
        (RuntimeError("private"), "llm_error"),
    ],
)
def test_single_model_failure(session, output, code):
    def reply(kwargs):
        if isinstance(output, Exception):
            raise output
        return output

    fake = ScriptedResponses([reply])
    with pytest.raises(ExecutionFailure, match=code) as exc:
        run(session, fake)
    assert len(fake.calls) == 1
    assert len([t for t in exc.value.traces if t.event_type == "tool_result"]) == 9
    assert len([t for t in exc.value.traces if t.event_type == "llm_response"]) == 1
    assert "private" not in json.dumps(
        [t.model_dump(mode="json") for t in exc.value.traces]
    )


@pytest.mark.parametrize(
    "usage", [None, {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150}]
)
def test_usage_and_step_time(session, usage):
    ticks = [0.0]

    def reply(kwargs):
        ticks[0] = 0.25
        return final() | {"usage": usage}

    _, traces = run(session, ScriptedResponses([reply]), clock=lambda: ticks[0])
    llm = traces[-1]
    assert llm.usage == usage and llm.duration_ms == 250
    assert llm.model == "provider-model" and llm.response_id == "resp-test"


@pytest.mark.parametrize("stage", ["before", "policies", "tool", "llm"])
def test_budget_stops_further_work(session, monkeypatch, stage):
    ticks = [0]
    original = customer.get_user

    def execute(*args, **kwargs):
        result = original(*args, **kwargs)
        if stage == "tool":
            ticks[0] = 2
        return result

    monkeypatch.setattr(customer, "get_user", execute)
    if stage == "policies":
        load = policy.load_policies

        def load_and_expire():
            docs = load()
            ticks[0] = 2
            return docs

        monkeypatch.setattr(policy, "load_policies", load_and_expire)

    def reply(kwargs):
        assert kwargs["timeout"] == 1
        ticks[0] = 2
        return final()

    fake = ScriptedResponses([reply])
    count = [0]

    def clock():
        count[0] += 1
        return 2 if stage == "before" and count[0] > 1 else ticks[0]

    with pytest.raises(ExecutionFailure, match="execution_timeout") as exc:
        run(
            session,
            fake,
            clock=clock,
            config=replace(CONFIG, workflow_timeout_seconds=1),
        )
    assert len(fake.calls) == (1 if stage == "llm" else 0)
    tools = [t for t in exc.value.traces if t.event_type == "tool_result"]
    assert len(tools) == (9 if stage == "llm" else 1 if stage == "tool" else 0)


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_invalid_budget(value):
    with pytest.raises(ValueError):
        replace(CONFIG, workflow_timeout_seconds=value)


def test_workflow_timeout_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused/test")
    monkeypatch.setenv("DEV_BEARER_TOKENS", json.dumps({"fake": str(uid(0x101))}))
    monkeypatch.setenv("WORKFLOW_TIMEOUT_SECONDS", "12.5")
    assert Settings.from_env().workflow_timeout_seconds == 12.5


def test_audit_prefetch_is_capped_and_reports_truncation(session):
    from app.models import AuditEvent

    session.add_all(
        AuditEvent(
            id=uid(0xB0000 + number),
            organization_id=uid(1),
            occurred_at=NOW - timedelta(minutes=number),
            actor_user_id=uid(0x101),
            event_type="user_login",
            target_user_id=None,
            payload={},
        )
        for number in range(1, 503)
    )
    # 14일 범위 밖 이벤트는 total_count에 포함하지 않는다.
    session.add(
        AuditEvent(
            id=uid(0xC0001),
            organization_id=uid(1),
            occurred_at=NOW - timedelta(days=15),
            actor_user_id=None,
            event_type="role_changed",
            target_user_id=uid(0x103),
            payload={},
        )
    )
    session.flush()
    fake = ScriptedResponses([final("diagnosed", [evidence(2)])])
    _, traces = run(session, fake)
    audit = next(t for t in traces if t.tool_name == "get_audit_events")
    data = audit.observation["data"]
    assert len(data["items"]) == 500
    assert data["total_count"] == 502 and data["truncated"] is True
    assert data["items"][0]["id"] == str(uid(0xB0001))
    assert len(fake.calls) == 1
    sent = json.loads(fake.calls[0]["input"][0]["content"])["observations"]
    assert sent[7]["observation"] == audit.observation
