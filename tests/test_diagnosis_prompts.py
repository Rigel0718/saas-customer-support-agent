import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent import loop
from app.agent.tools import TOOL_DEFINITIONS
from app.config import Settings
from app.schemas.support import DiagnosisDTO
from app.seed import uid
from app.services import customer
from app.workflow import runner
from tests.test_agent_loop import ScriptedResponses, evidence, final, function, response
from tests.test_support_models import NOW


# Captured before the runtime refactor; do not derive these from the current constants.
EXPECTED_INSTRUCTIONS = json.loads(
    (Path(__file__).parent / "fixtures" / "diagnosis_instructions.json").read_text()
)


@pytest.mark.parametrize("mode", ["agent", "workflow"])
def test_final_instructions_match_pre_refactor_text(mode):
    module = loop if mode == "agent" else runner
    assert module.INSTRUCTIONS == EXPECTED_INSTRUCTIONS[mode]


@pytest.mark.parametrize("mode", ["agent", "workflow"])
def test_model_requests_preserve_instructions_options_and_target(session, mode):
    replies = (
        [response([function()]), final("diagnosed", [evidence(3)])]
        if mode == "agent"
        else [final("diagnosed", [evidence(2)])]
    )
    fake = ScriptedResponses(replies)
    traces = []
    run = loop.run_agent if mode == "agent" else runner.run_workflow
    run(
        session,
        customer.CallerContext(uid(0x102), uid(1), "admin"),
        SimpleNamespace(requester_user_id=uid(0x103), content="초대 문의"),
        Settings("unused", {}, openai_model="scripted-model"),
        traces,
        create=fake,
        clock=lambda: 0,
        now=lambda: NOW,
    )
    assert len(fake.calls) == (2 if mode == "agent" else 1)
    keys = {"model", "instructions", "input", "store", "timeout", "text"}
    if mode == "agent":
        keys |= {"tools", "parallel_tool_calls", "include"}
    for call in fake.calls:
        assert set(call) == keys
        assert call["instructions"] == EXPECTED_INSTRUCTIONS[mode]
        assert call["model"] == "scripted-model"
        assert call["store"] is False and call["timeout"] == 20
        assert call["text"] == {
            "format": {
                "type": "json_schema",
                "name": "support_diagnosis",
                "strict": True,
                "schema": DiagnosisDTO.model_json_schema(),
            }
        }
        target = json.loads(call["input"][0]["content"])
        assert target["requester_user_id"] == str(uid(0x103))
        assert target["organization_id"] == str(uid(1))
        assert target["reference_utc"] == NOW.isoformat()
        assert target["content"] == "초대 문의"
        if mode == "agent":
            assert set(target) == {
                "requester_user_id",
                "organization_id",
                "reference_utc",
                "content",
            }
            assert call["tools"] == TOOL_DEFINITIONS
            assert call["parallel_tool_calls"] is False
            assert call["include"] == ["reasoning.encrypted_content"]
        else:
            assert set(target) == {
                "requester_user_id",
                "organization_id",
                "reference_utc",
                "content",
                "observations",
            }
            observations = target["observations"]
            assert [item["tool_name"] for item in observations] == [
                "get_user",
                "get_organization",
                "get_subscription",
                "get_payment_history",
                "search_policy",
                "search_policy",
                "search_policy",
            ]
            assert [item["observation"] for item in observations] == [
                trace.observation
                for trace in traces
                if trace.event_type == "tool_result"
            ]
