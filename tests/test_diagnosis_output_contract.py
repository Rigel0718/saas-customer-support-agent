"""Fake 응답으로 공통 지침 전달과 결과 보존을 검증한다. 모델의 판단 품질은 평가하지 않는다."""

import json
from types import SimpleNamespace

import pytest

from app.agent import loop
from app.config import Settings
from app.diagnosis.prompts import OUTPUT_CONTRACT
from app.diagnosis.runtime import add_trace
from app.diagnosis.tools import ToolResult
from app.seed import uid
from app.services import customer, support
from app.workflow import runner
from tests.test_agent_loop import ScriptedResponses, evidence, final, function, response
from tests.test_support_models import NOW


@pytest.mark.parametrize("mode", ["agent", "workflow"])
@pytest.mark.parametrize(
    "outcome,causes,summary,next_actions",
    [
        pytest.param(
            "needs_information",
            [],
            "현재 관찰만으로는 원인을 확정할 수 없습니다.",
            ["오류 메시지와 발생 시각을 알려 주세요."],
            id="no-confirmed-cause",
        ),
        pytest.param(
            "diagnosed",
            ["구독 만료"],
            "만료된 구독으로 기능을 사용할 수 없습니다.",
            ["관리자에게 구독 갱신을 요청하세요."],
            id="one-confirmed-cause",
        ),
        pytest.param(
            "diagnosed",
            ["구독 만료", "사용자 권한 부족"],
            "구독 만료와 권한 부족이 모두 확인되었습니다.",
            ["구독을 갱신하고 관리자에게 권한을 요청하세요."],
            id="multiple-confirmed-causes",
        ),
        pytest.param(
            "needs_information",
            ["구독 만료"],
            "구독 만료는 확인되었으나 별도로 보고된 오류는 추가 정보가 필요합니다.",
            ["별도 오류의 메시지와 재현 절차를 알려 주세요."],
            id="confirmed-cause-with-missing-information",
        ),
        pytest.param(
            "needs_information",
            ["구독 만료", "사용자 권한 부족"],
            "구독 만료와 권한 부족은 확인되었으나 별도 오류는 추가 확인이 필요합니다.",
            ["별도 오류의 메시지를 알려 주세요.", "발생 시각을 알려 주세요."],
            id="multiple-confirmed-causes-with-missing-information",
        ),
    ],
)
def test_modes_preserve_diagnosis_contract(
    monkeypatch, mode, outcome, causes, summary, next_actions
):
    module = loop if mode == "agent" else runner

    def observe(name, raw_arguments, session, context, documents, traces, budget, **kw):
        # 조회 경계만 대체하며 실제 Loop/Workflow, LLM 어댑터, 결과 검증은 사용한다.
        data = {
            "get_user": {
                "role": "member" if "사용자 권한 부족" in causes else "admin"
            },
            "get_subscription": {
                "status": "expired" if "구독 만료" in causes else "active"
            },
        }.get(name, {"items": []})
        observation = {
            "ok": True,
            "data": data,
            "error_code": None,
            "trace_sequence": len(traces) + 1,
        }
        arguments = json.loads(raw_arguments)
        add_trace(
            traces,
            "tool_result",
            kw["now"],
            tool_name=name,
            arguments=arguments,
            observation=observation,
            step=kw["step"],
            call_id=kw.get("call_id"),
        )
        return ToolResult(arguments, observation)

    monkeypatch.setattr(module, "execute_observed_tool", observe)
    traces = []

    def reply(kwargs):
        assert OUTPUT_CONTRACT in kwargs["instructions"]
        return final(
            outcome,
            [evidence(t.sequence) for t in traces if t.event_type == "tool_result"],
            summary=summary,
            causes=causes,
            next_actions=next_actions,
        )

    replies = (
        [
            response([function("get_user", {"user_id": str(uid(0x103))})]),
            response([function(call_id="subscription")]),
            reply,
        ]
        if mode == "agent"
        else [reply]
    )
    fake = ScriptedResponses(replies)
    run = loop.run_agent if mode == "agent" else runner.run_workflow
    raw = run(
        None,
        customer.CallerContext(uid(0x102), uid(1), "admin"),
        SimpleNamespace(requester_user_id=uid(0x103), content="기능을 사용할 수 없습니다."),
        Settings("unused", {}, openai_model="scripted-model"),
        traces,
        create=fake,
        clock=lambda: 0,
        now=lambda: NOW,
    )
    result = support.validate_diagnosis(raw, traces)
    assert result.model_dump(mode="json") == json.loads(raw)
    assert result.outcome == outcome
    assert result.causes == causes
    assert result.next_actions == next_actions
    assert all(OUTPUT_CONTRACT in call["instructions"] for call in fake.calls)
