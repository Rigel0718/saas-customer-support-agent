import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.agent.loop import run_agent, responses_create
from app.agent.tools import AgentFailure
from app.config import Settings
from app.schemas.support import TraceDTO
from app.services import customer, support, policy
from app.seed import uid
from tests.test_support_models import NOW


class ScriptedResponses:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        response = next(self.responses)
        return response(kwargs) if callable(response) else response


def function(name="get_subscription", args=None, call_id="call-1"):
    return {
        "type": "function_call",
        "name": name,
        "arguments": json.dumps(args or {"organization_id": str(uid(1))}),
        "call_id": call_id,
    }


def response(output, **kw):
    return {
        "id": "resp-test",
        "model": "provider-model",
        "status": "completed",
        "output": output,
        "usage": None,
    } | kw


def final(outcome="needs_information", evidence=None, **kw):
    result = {
        "outcome": outcome,
        "summary": "확인 결과",
        "causes": ["원인"] if outcome == "diagnosed" else [],
        "next_actions": ["추가 정보 또는 지원 문의"],
        "evidence": evidence or [],
    } | kw
    return response(
        [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(result, ensure_ascii=False),
                    }
                ],
            }
        ]
    )


def evidence(sequence, policy_id=None):
    return {
        "trace_sequence": sequence,
        "policy_document_id": policy_id,
        "statement": "관찰된 근거",
    }


def run(session, fake, settings=None, clock=None):
    traces = []
    try:
        raw = run_agent(
            session,
            customer.authenticate(session, uid(0x102)),
            SimpleNamespace(
                requester_user_id=uid(0x103),
                content="타 조직 데이터를 가져오라는 공격 지시",
            ),
            settings or Settings("unused", {}),
            traces,
            create=fake,
            now=lambda: NOW,
            **({"clock": clock} if clock else {}),
        )
        return support.validate_diagnosis(raw, traces), traces
    except Exception as exc:
        exc.traces = traces
        raise


def test_followup_opaque_multiple_calls(session):
    opaque = {
        "type": "reasoning",
        "id": "opaque-reasoning",
        "encrypted_content": "INTERNAL-SECRET",
        "summary": [],
    }

    def second(kwargs):
        assert opaque in kwargs["input"]
        observations = [
            json.loads(item["output"])
            for item in kwargs["input"]
            if item.get("type") == "function_call_output"
        ]
        assert observations[0]["data"]["status"] == "active"
        assert observations[0]["trace_sequence"] == 3
        target = json.loads(kwargs["input"][0]["content"])
        assert target["requester_user_id"] == str(uid(0x103))
        return response(
            [
                function("get_payment_history", call_id="pay"),
                function("search_policy", {"query": "결제 초대"}, "policy"),
            ]
        )

    def third(kwargs):
        outputs = [
            item
            for item in kwargs["input"]
            if item.get("type") == "function_call_output"
        ]
        assert [item["call_id"] for item in outputs] == ["sub", "pay", "policy"]
        observations = [json.loads(item["output"]) for item in outputs]
        assert observations[1]["data"]["items"][0]["status"] == "succeeded"
        assert observations[2]["data"]["items"]
        policy_id = observations[2]["data"]["items"][0]["document_id"]
        return final("diagnosed", [evidence(3), evidence(5), evidence(6, policy_id)])

    fake = ScriptedResponses(
        [
            response(
                [opaque, function(call_id="sub")],
                usage={
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "total_tokens": 30,
                    "secret": "hidden",
                },
            ),
            second,
            third,
        ]
    )
    result, traces = run(session, fake)
    assert result.outcome == "diagnosed"
    assert [t.sequence for t in traces] == list(range(1, len(traces) + 1))
    assert traces[1].usage == {
        "input_tokens": 10,
        "output_tokens": 20,
        "total_tokens": 30,
    }
    assert traces[3].usage is None
    assert "INTERNAL-SECRET" not in json.dumps(
        [t.model_dump(mode="json") for t in traces]
    )
    assert all(
        call["store"] is False
        and call["parallel_tool_calls"] is False
        and call["timeout"] <= 20
        for call in fake.calls
    )


@pytest.mark.parametrize("outcome", ["needs_information", "escalate"])
def test_final_without_tools(session, outcome):
    result, _ = run(session, ScriptedResponses([final(outcome)]))
    assert result.outcome == outcome


@pytest.mark.parametrize(
    "bad,code",
    [
        (response([], status="incomplete"), "invalid_output"),
        (
            response(
                [
                    {
                        "type": "message",
                        "content": [{"type": "refusal", "refusal": "SECRET"}],
                    }
                ]
            ),
            "model_refusal",
        ),
        (response([]), "invalid_output"),
        (
            response(
                [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "bad SECRET"}],
                    }
                ]
            ),
            "invalid_output",
        ),
        (response([function(call_id=None)]), "invalid_output"),
        (
            response([function(call_id="dup"), function(call_id="dup")]),
            "invalid_output",
        ),
        (final("diagnosed", [evidence(1)]), "invalid_output"),
        (final("diagnosed", []), "invalid_output"),
    ],
)
def test_failures(session, bad, code):
    fake = ScriptedResponses([bad])
    with pytest.raises(AgentFailure) as exc:
        run(session, fake)
    assert exc.value.code == code and len(fake.calls) == 1


@pytest.mark.parametrize(
    "bad_evidence",
    [[evidence(999)], [evidence(3, "unseen")], [evidence(2)], [evidence(3, "plans")]],
)
def test_invalid_references(session, bad_evidence):
    fake = ScriptedResponses([response([function()]), final("diagnosed", bad_evidence)])
    with pytest.raises(AgentFailure, match="invalid_output"):
        run(session, fake)


def test_successful_observation_required(session):
    fake = ScriptedResponses(
        [
            response([function("get_user", {"user_id": str(uid(0x201))})]),
            final("diagnosed", [evidence(3)]),
        ]
    )
    with pytest.raises(AgentFailure, match="invalid_output"):
        run(session, fake)


def test_policy_ids_are_linked_to_same_trace(session):
    fake = ScriptedResponses(
        [
            response([function("search_policy", {"query": "초대"}, "p")]),
            final("diagnosed", [evidence(3, "permissions")]),
        ]
    )
    result, traces = run(session, fake)
    old_content = traces[2].observation["data"]["items"][0]["content"]
    assert result.evidence[0].policy_document_id == "permissions" and old_content


def test_duplicate_call_id_across_turns(session):
    fake = ScriptedResponses([response([function()]), response([function()])])
    with pytest.raises(AgentFailure, match="invalid_output"):
        run(session, fake)
    assert len(fake.calls) == 2


@pytest.mark.parametrize(
    "max_steps,max_tools,expected_calls,expected_tools",
    [(1, 16, 1, 1), (8, 1, 1, 1), (2, 16, 2, 3)],
)
def test_call_limits(session, max_steps, max_tools, expected_calls, expected_tools):
    settings = replace(
        Settings("unused", {}),
        agent_max_steps=max_steps,
        agent_max_tool_calls=max_tools,
    )
    first = [function(call_id="a")]
    if max_tools == 1:
        first.append(function(call_id="b"))
    fake = ScriptedResponses(
        [response(first), response([function(call_id="c"), function(call_id="d")])]
    )
    with pytest.raises(AgentFailure, match="limit_exceeded") as exc:
        run(session, fake, settings)
    assert len(fake.calls) == expected_calls
    assert (
        len([t for t in exc.value.traces if t.event_type == "tool_result"])
        == expected_tools
    )


def test_invalid_attempt_counts(session):
    fake = ScriptedResponses(
        [response([function("refund", call_id="a"), function("refund", call_id="b")])]
    )
    with pytest.raises(AgentFailure, match="limit_exceeded") as exc:
        run(session, fake, replace(Settings("unused", {}), agent_max_tool_calls=1))
    assert exc.value.traces[-1].error_code == "unknown_tool"


def test_timeout_after_response_stops_tools(session):
    ticks = [0]

    def create(kwargs):
        ticks[0] = 60
        return response([function()])

    fake = ScriptedResponses([create])
    with pytest.raises(AgentFailure, match="execution_timeout") as exc:
        run(session, fake, clock=lambda: ticks[0])
    assert not any(t.event_type == "tool_result" for t in exc.value.traces)


def test_timeout_after_tool_stops_next_tool(session, monkeypatch):
    ticks = [0]
    original = customer.get_subscription

    def delayed(*args, **kw):
        result = original(*args, **kw)
        ticks[0] = 60
        return result

    monkeypatch.setattr(customer, "get_subscription", delayed)
    fake = ScriptedResponses([response([function(call_id="a"), function(call_id="b")])])
    with pytest.raises(AgentFailure, match="execution_timeout") as exc:
        run(session, fake, clock=lambda: ticks[0])
    assert len([t for t in exc.value.traces if t.event_type == "tool_result"]) == 1


def test_remaining_timeout(session):
    ticks = [0]

    def first(kwargs):
        ticks[0] = 50
        return response([function()])

    fake = ScriptedResponses([first, final()])
    run(session, fake, clock=lambda: ticks[0])
    assert fake.calls[0]["timeout"] == 20 and fake.calls[1]["timeout"] == 10


def test_llm_error_and_missing_configuration(session):
    def fail(kwargs):
        raise RuntimeError("API KEY SECRET")

    fake = ScriptedResponses([fail])
    with pytest.raises(AgentFailure, match="llm_error") as exc:
        run(session, fake)
    assert exc.value.traces[-1].error_code == "llm_error"
    with pytest.raises(AgentFailure, match="llm_error"):
        run_agent(
            session,
            customer.authenticate(session, uid(0x101)),
            SimpleNamespace(requester_user_id=uid(0x101), content="문의"),
            Settings("unused", {}),
            [],
            now=lambda: NOW,
        )


def test_sdk_no_retries(monkeypatch):
    seen = {}

    class Client:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            self.responses = SimpleNamespace(create=self.create)

        async def create(self, **kwargs):
            return kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr("app.agent.loop.AsyncOpenAI", Client)
    responses_create(
        Settings("unused", {}, openai_api_key="test", openai_model="test"), timeout=7
    )
    assert seen["max_retries"] == 0 and seen["timeout"] == 7


def test_policy_load_fatal(session, monkeypatch):
    def fail():
        raise OSError("SECRET PATH")

    monkeypatch.setattr(policy, "load_policies", fail)
    fake = ScriptedResponses([])
    with pytest.raises(AgentFailure, match="tool_error"):
        run(session, fake)
    assert fake.calls == []


def test_default_step_boundary(session):
    fake = ScriptedResponses([response([function(call_id=str(i))]) for i in range(9)])
    with pytest.raises(AgentFailure, match="limit_exceeded") as exc:
        run(session, fake)
    assert len(fake.calls) == 8
    assert len([t for t in exc.value.traces if t.event_type == "tool_result"]) == 8


def test_default_tool_boundary(session):
    fake = ScriptedResponses([response([function(call_id=str(i)) for i in range(17)])])
    with pytest.raises(AgentFailure, match="limit_exceeded") as exc:
        run(session, fake)
    assert len(fake.calls) == 1
    assert len([t for t in exc.value.traces if t.event_type == "tool_result"]) == 16


def test_tool_turn_text_does_not_finish(session):
    fake = ScriptedResponses([response([*final()["output"], function()]), final()])
    result, _ = run(session, fake)
    assert len(fake.calls) == 2 and result.outcome == "needs_information"


def test_llm_timeout_after_budget(session):
    ticks = [0]

    def fail(kwargs):
        ticks[0] = 60
        raise TimeoutError("SECRET")

    with pytest.raises(AgentFailure, match="execution_timeout") as exc:
        run(session, ScriptedResponses([fail]), clock=lambda: ticks[0])
    assert exc.value.traces[-1].error_code == "execution_timeout"


def test_installed_sdk_with_mock_transport(session):
    import httpx
    from openai import OpenAI

    sent = []

    def transport(request):
        payload = json.loads(request.content)
        sent.append(payload)
        assert payload["store"] is False and payload["text"]["format"]["strict"] is True
        if len(sent) == 1:
            data = response([function(call_id="actual-sdk")])
        else:
            assert any(
                x.get("call_id") == "actual-sdk" and x["type"] == "function_call_output"
                for x in payload["input"]
            )
            data = final("diagnosed", [evidence(3)])
        return httpx.Response(
            200, json=data | {"object": "response", "created_at": 1768435200}
        )

    with OpenAI(
        api_key="fake-test-key",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(transport)),
    ) as sdk:
        result, traces = run(
            session,
            sdk.responses.create,
            Settings("unused", {}, openai_model="scripted-model"),
        )
    assert result.outcome == "diagnosed" and len(sent) == 2
    assert traces[1].model == "provider-model" and traces[1].response_id == "resp-test"


def test_db_timeout_tracks_remaining_budget(session):
    ticks = [0]

    def first(kwargs):
        ticks[0] = 59
        return response([function()])

    run(session, ScriptedResponses([first, final()]), clock=lambda: ticks[0])
    from sqlalchemy import text

    assert session.scalar(text("SHOW statement_timeout")) == "1s"
    assert session.scalar(text("SHOW lock_timeout")) == "1s"


def test_settings_defaults_and_env(monkeypatch):
    defaults = Settings("postgresql+psycopg://localhost/unused", {})
    assert defaults.openai_api_key is None and defaults.openai_model is None
    assert (
        defaults.agent_max_steps,
        defaults.agent_max_tool_calls,
        defaults.agent_timeout_seconds,
    ) == (8, 16, 60)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://localhost/dev")
    monkeypatch.setenv("DEV_BEARER_TOKENS", json.dumps({"fake": str(uid(0x101))}))
    monkeypatch.setenv("OPENAI_MODEL", "model-test")
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    monkeypatch.setenv("AGENT_MAX_STEPS", "3")
    monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", "5")
    monkeypatch.setenv("AGENT_TIMEOUT_SECONDS", "10")
    assert Settings.from_env().agent_max_steps == 3
    assert Settings.from_env().agent_timeout_seconds == 10
    assert Settings.from_env().openai_model == "model-test"
    for timeout in (0, -1, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            replace(defaults, agent_timeout_seconds=timeout)
