"""Output rejection boundaries with Fake Responses; no live LLM calls."""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.agent import loop
from app.diagnosis.llm import call_model
from app.diagnosis.runtime import ExecutionBudget, ExecutionFailure, add_trace
from app.diagnosis.tools import ToolResult
from app.seed import uid
from app.services.customer import CallerContext
from app.services import support
from app.workflow import runner
from app.schemas.support import (
    DiagnosisDTO,
    FailureMetadata,
    TraceDTO,
    diagnosis_field_path,
)
from tests.test_support_models import NOW
from tests.test_agent_loop import ScriptedResponses, evidence, final, function, response
from app.diagnosis.llm import extract_output_text


FAILURE_PAIRS = [
    ("response_status", "not_completed"),
    ("agent_tool_call", "invalid_call_id"),
    ("agent_tool_call", "duplicate_call_id"),
    ("workflow_output", "unexpected_function_call"),
    ("text_extraction", "empty_output"),
    ("diagnosis_json", "invalid_json"),
    ("diagnosis_schema", "validation_error"),
    ("evidence_reference", "invalid_trace_reference"),
    ("evidence_reference", "no_successful_observation"),
    ("policy_reference", "invalid_policy_reference"),
    ("policy_reference", "unobserved_policy_document"),
    ("observation_validation", "invalid_observation"),
]


@pytest.mark.parametrize("stage,reason", FAILURE_PAIRS)
def test_failure_metadata_roundtrip(stage, reason):
    metadata = FailureMetadata(failure_stage=stage, failure_reason=reason)
    assert FailureMetadata.model_validate_json(metadata.model_dump_json()) == metadata
    assert metadata.field_path is None


@pytest.mark.parametrize(
    "changes",
    [
        {"failure_stage": "unknown"},
        {"failure_reason": "unknown"},
        {"failure_reason": "invalid_call_id"},
        {"message": "sensitive-marker"},
        {"field_path": "sensitive-marker"},
        {"field_path": "evidence[-1].statement"},
        {"field_path": "evidence[True].statement"},
        {"field_path": "summary[0]"},
        {"field_path": "summary\n"},
        {"field_path": True},
    ],
)
def test_failure_metadata_rejects_unknown_values(changes):
    with pytest.raises(ValidationError):
        FailureMetadata.model_validate(
            {"failure_stage": "response_status", "failure_reason": "not_completed"}
            | changes
        )


@pytest.mark.parametrize(
    "location,expected",
    [
        ((), None),
        (("summary",), "summary"),
        (("outcome",), "outcome"),
        (("causes", 0), "causes[0]"),
        (("next_actions", 2), "next_actions[2]"),
        (("evidence",), "evidence"),
        (("evidence", 0), "evidence[0]"),
        (("evidence", 0, "trace_sequence"), "evidence[0].trace_sequence"),
        (("evidence", 0, "policy_document_id"), "evidence[0].policy_document_id"),
        (("evidence", 0, "statement"), "evidence[0].statement"),
        (("sensitive-marker",), None),
        (("evidence", 0, "sensitive-marker"), None),
        (("evidence", True), None),
        (("evidence", -1), None),
        (("evidence", "0"), None),
        (("summary", 0), None),
        (("causes", 0, "statement"), None),
    ],
)
def test_only_schema_field_paths_are_preserved(location, expected):
    assert diagnosis_field_path(location) == expected
    metadata = FailureMetadata(
        failure_stage="diagnosis_schema", failure_reason="validation_error",
        field_path=expected,
    )
    assert metadata.field_path == expected


def test_legacy_trace_and_diagnosis_schema_unchanged():
    trace = TraceDTO.model_validate(
        {"sequence": 1, "event_type": "execution_started", "created_at": NOW}
    )
    assert trace.failure_metadata is None
    # Snapshot of the approved model-facing contract before instrumentation.
    encoded = json.dumps(DiagnosisDTO.model_json_schema(), sort_keys=True).encode()
    assert hashlib.sha256(encoded).hexdigest() == (
        "29666c9d9f58c66edb377729aefa796298f5d2e7d17974aa52d68d38da257bc8"
    )


def test_execution_failure_keeps_legacy_message_and_optional_metadata():
    legacy = ExecutionFailure("invalid_output")
    assert legacy.code == str(legacy) == "invalid_output"
    assert legacy.failure_metadata is None
    metadata = FailureMetadata(
        failure_stage="diagnosis_schema", failure_reason="validation_error",
        field_path="summary",
    )
    failure = ExecutionFailure("invalid_output", failure_metadata=metadata)
    assert failure.failure_metadata == metadata
    assert str(failure) == "invalid_output"
    assert "summary" not in str(failure)


@pytest.mark.parametrize("status", ["incomplete", None, "failed"])
@pytest.mark.parametrize("priority", [None, "model_refusal", "execution_timeout"])
def test_response_status_classification_preserves_priorities(status, priority):
    ticks = [0]
    budget = ExecutionBudget(10, lambda: ticks[0])
    traces = []

    def reply(kwargs):
        output = response([], status=status)
        if status is None:
            output.pop("status")
        if priority == "model_refusal":
            output["output"] = [
                {"type": "message", "content": [
                    {"type": "refusal", "refusal": "sensitive-marker"}
                ]}
            ]
        if priority == "execution_timeout":
            ticks[0] = 10
        return output

    fake = ScriptedResponses([reply])
    with pytest.raises(ExecutionFailure) as caught:
        call_model(
            Settings("unused", {}, openai_model="fake"), [], "instructions",
            traces, budget, step=1, create=fake, now=lambda: NOW,
        )
    failure = caught.value
    assert failure.code == (priority or "invalid_output")
    assert len(fake.calls) == len(traces) == 1
    assert traces[0].failure_metadata is None
    assert traces[0].error_code is None
    assert traces[0].llm_metadata.error is None
    if priority:
        assert failure.failure_metadata is None
    else:
        assert failure.failure_metadata == FailureMetadata(
            failure_stage="response_status", failure_reason="not_completed"
        )
    assert "sensitive-marker" not in str(failure) + traces[0].model_dump_json()


@pytest.fixture
def stub_tools(monkeypatch):
    def observe(name, raw_arguments, session, context, documents, traces, budget, **kw):
        arguments = json.loads(raw_arguments)
        observation = {
            "ok": True, "data": {"items": []}, "error_code": None,
            "trace_sequence": len(traces) + 1,
        }
        add_trace(
            traces, "tool_result", kw["now"], tool_name=name,
            step=kw["step"], call_id=kw.get("call_id"), arguments=arguments,
            observation=observation,
        )
        return ToolResult(arguments, observation)

    monkeypatch.setattr(loop, "execute_observed_tool", observe)
    monkeypatch.setattr(runner, "execute_observed_tool", observe)


def run_mode(mode, fake, traces):
    run = loop.run_agent if mode == "agent" else runner.run_workflow
    return run(
        None, CallerContext(uid(0x102), uid(1), "admin"),
        SimpleNamespace(requester_user_id=uid(0x103), content="문의"),
        Settings("unused", {}, openai_model="fake"), traces,
        create=fake, clock=lambda: 0, now=lambda: NOW,
    )


@pytest.mark.parametrize("call_id", [None, "", "  ", 1, True, [], {}])
def test_agent_invalid_call_id_is_fail_fast(stub_tools, call_id):
    bad = function(call_id=call_id)
    if call_id is None:
        bad.pop("call_id")
    fake = ScriptedResponses([response([bad]), final()])
    traces = []
    with pytest.raises(ExecutionFailure) as caught:
        run_mode("agent", fake, traces)
    assert caught.value.code == "invalid_output"
    assert caught.value.failure_metadata.failure_reason == "invalid_call_id"
    assert len(fake.calls) == 1
    assert [t.event_type for t in traces] == ["execution_started", "llm_response"]


@pytest.mark.parametrize("across_turns", [False, True])
def test_agent_duplicate_ids_before_tools(stub_tools, across_turns):
    calls = [function(call_id="dup"), function(call_id="dup")]
    script = [response([calls[0]]), response([calls[1]])] if across_turns else [response(calls)]
    fake = ScriptedResponses(script + [final()])
    traces = []
    with pytest.raises(ExecutionFailure) as caught:
        run_mode("agent", fake, traces)
    assert caught.value.failure_metadata.failure_reason == "duplicate_call_id"
    assert len(fake.calls) == (2 if across_turns else 1)
    assert sum(t.event_type == "tool_result" for t in traces) == int(across_turns)


def test_agent_invalid_id_precedes_duplicate(stub_tools):
    fake = ScriptedResponses([response([
        function(call_id="dup"), function(call_id="dup"), function(call_id=[])
    ])])
    with pytest.raises(ExecutionFailure) as caught:
        run_mode("agent", fake, [])
    assert caught.value.failure_metadata.failure_reason == "invalid_call_id"


@pytest.fixture
def service_without_db(monkeypatch, stub_tools):
    """Exercise the real Service failure boundary; storage has separate DB tests."""
    request = SimpleNamespace(requester_user_id=uid(0x103), content="문의")
    monkeypatch.setattr(support, "get_request", lambda *args: request)

    def terminal(session, context, request_id, diagnosis, code, traces, now):
        return SimpleNamespace(diagnosis=diagnosis, error_code=code, traces=traces)

    monkeypatch.setattr(support, "save_terminal", terminal)
    session = MagicMock()
    session.execute.return_value.rowcount = 1

    def diagnose(mode, fake):
        return support.diagnose_request(
            session, CallerContext(uid(0x102), uid(1), "admin"), uid(999),
            Settings("unused", {}, openai_model="fake"), create=fake,
            clock=lambda: 0, now=lambda: NOW, mode=mode,
        )

    return diagnose


def text_response(text):
    return response([{"type": "message", "content": [
        {"type": "output_text", "text": text}
    ]}])


@pytest.mark.parametrize(
    "output,stage,reason",
    [
        (response([function()]), "workflow_output", "unexpected_function_call"),
        (response([]), "text_extraction", "empty_output"),
        (text_response("  \n"), "text_extraction", "empty_output"),
    ],
)
def test_workflow_output_failures(stub_tools, output, stage, reason):
    fake = ScriptedResponses([output, final()])
    traces = []
    with pytest.raises(ExecutionFailure) as caught:
        run_mode("workflow", fake, traces)
    assert caught.value.code == "invalid_output"
    assert caught.value.failure_metadata == FailureMetadata(
        failure_stage=stage, failure_reason=reason
    )
    assert len(fake.calls) == 1
    assert sum(t.event_type == "tool_result" for t in traces) == 9
    assert sum(t.event_type == "llm_response" for t in traces) == 1


@pytest.mark.parametrize("mode", ["agent", "workflow"])
@pytest.mark.parametrize("output", [response([None]), text_response(None)])
def test_malformed_response_keeps_tool_error(service_without_db, mode, output):
    fake = ScriptedResponses([output])
    result = service_without_db(mode, fake)
    assert result.error_code == "tool_error"
    assert result.diagnosis is None
    assert all(t.failure_metadata is None for t in result.traces)
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    "raw,stage,reason,path",
    [
        ("", "text_extraction", "empty_output", None),
        ("  \n", "text_extraction", "empty_output", None),
        ("{", "diagnosis_json", "invalid_json", None),
        ("sensitive-marker", "diagnosis_json", "invalid_json", None),
        ("[]", "diagnosis_schema", "validation_error", None),
        ("{}", "diagnosis_schema", "validation_error", "outcome"),
        (
            extract_output_text(final(summary="  ")),
            "diagnosis_schema",
            "validation_error",
            "summary",
        ),
        (
            extract_output_text(final(summary=4)),
            "diagnosis_schema",
            "validation_error",
            "summary",
        ),
        (
            extract_output_text(final(outcome="unknown")),
            "diagnosis_schema",
            "validation_error",
            "outcome",
        ),
        (
            extract_output_text(final(causes=["  "])),
            "diagnosis_schema",
            "validation_error",
            "causes[0]",
        ),
        (
            extract_output_text(final(next_actions=[])),
            "diagnosis_schema",
            "validation_error",
            None,
        ),
        (
            extract_output_text(final("diagnosed")),
            "diagnosis_schema",
            "validation_error",
            None,
        ),
        (
            extract_output_text(final(**{"sensitive-marker": "sensitive-marker"})),
            "diagnosis_schema",
            "validation_error",
            None,
        ),
    ],
)
def test_json_schema_classification(raw, stage, reason, path, caplog):
    with pytest.raises(ExecutionFailure) as caught:
        support.validate_diagnosis(raw, [])
    assert caught.value.code == str(caught.value) == "invalid_output"
    assert caught.value.failure_metadata == FailureMetadata(
        failure_stage=stage, failure_reason=reason, field_path=path
    )
    assert "sensitive-marker" not in (
        caught.value.failure_metadata.model_dump_json() + str(caught.value) + caplog.text
    )


@pytest.mark.parametrize(
    "changes,path",
    [
        ({"evidence": [evidence(True)]}, "evidence[0].trace_sequence"),
        ({"evidence": [evidence("sensitive-marker")]}, "evidence[0].trace_sequence"),
        ({"evidence": [evidence(1) | {"sensitive-marker": 1}]}, None),
        ({"summary": "", "causes": [""]}, "summary"),
    ],
)
def test_first_schema_error_is_safely_projected(changes, path, caplog):
    raw = extract_output_text(final(**changes))
    with pytest.raises(ExecutionFailure) as caught:
        support.validate_diagnosis(raw, [])
    metadata = caught.value.failure_metadata
    assert metadata.failure_stage == "diagnosis_schema"
    assert metadata.field_path == path
    assert "sensitive-marker" not in metadata.model_dump_json() + str(caught.value) + caplog.text


def test_schema_context_is_never_persisted(monkeypatch, caplog):
    error = ValidationError.from_exception_data("DiagnosisDTO", [{
        "type": "value_error", "loc": (), "input": "sensitive-marker",
        "ctx": {"error": ValueError("sensitive-marker")},
    }])

    def reject(*args, **kwargs):
        raise error

    monkeypatch.setattr(DiagnosisDTO, "model_validate_json", reject)
    with pytest.raises(ExecutionFailure) as caught:
        support.validate_diagnosis("{}", [])
    assert caught.value.failure_metadata.field_path is None
    assert "sensitive-marker" not in caught.value.failure_metadata.model_dump_json() + str(caught.value) + caplog.text


def observation_trace(sequence=3, *, tool="get_user", observation=None, event="tool_result"):
    return TraceDTO(
        sequence=sequence, step=1, event_type=event, tool_name=tool,
        observation={"ok": True, "data": {}, "trace_sequence": sequence}
        if observation is None else observation, created_at=NOW,
    )


@pytest.mark.parametrize(
    "ref,traces,stage,reason,path",
    [
        (
            evidence(1),
            [observation_trace()],
            "evidence_reference",
            "invalid_trace_reference",
            "evidence[0].trace_sequence",
        ),
        (
            evidence(3),
            [observation_trace(event="llm_response")],
            "evidence_reference",
            "invalid_trace_reference",
            "evidence[0].trace_sequence",
        ),
        (
            evidence(3),
            [observation_trace(observation={"ok": False})],
            "evidence_reference",
            "no_successful_observation",
            "evidence",
        ),
        (
            evidence(3, "sensitive-marker"),
            [observation_trace()],
            "policy_reference",
            "invalid_policy_reference",
            "evidence[0].policy_document_id",
        ),
        (
            evidence(3, "plans"),
            [observation_trace(tool="search_policy", observation={"ok": False})],
            "policy_reference",
            "invalid_policy_reference",
            "evidence[0].policy_document_id",
        ),
        (
            evidence(3, "plans"),
            [
                observation_trace(
                    tool="search_policy",
                    observation={"ok": True, "data": {"items": []}},
                ),
                observation_trace(
                    4,
                    tool="search_policy",
                    observation={"ok": True, "data": {"items": [{"document_id": "plans"}]}},
                ),
            ],
            "policy_reference",
            "unobserved_policy_document",
            "evidence[0].policy_document_id",
        ),
        (
            evidence(3),
            [observation_trace(observation={})],
            "observation_validation",
            "invalid_observation",
            "evidence[0]",
        ),
        (
            evidence(3, "plans"),
            [
                observation_trace(
                    tool="search_policy",
                    observation={"ok": True, "data": None},
                ),
            ],
            "observation_validation",
            "invalid_observation",
            "evidence[0].policy_document_id",
        ),
        (
            evidence(3, "plans"),
            [
                observation_trace(
                    tool="search_policy",
                    observation={"ok": True, "data": {"items": [{}]}},
                ),
            ],
            "observation_validation",
            "invalid_observation",
            "evidence[0].policy_document_id",
        ),
    ],
)
def test_reference_failures(ref, traces, stage, reason, path, caplog):
    raw = extract_output_text(final("diagnosed", [ref]))
    with pytest.raises(ExecutionFailure) as caught:
        support.validate_diagnosis(raw, traces)
    assert caught.value.code == "invalid_output"
    assert caught.value.failure_metadata == FailureMetadata(
        failure_stage=stage, failure_reason=reason, field_path=path
    )
    assert "sensitive-marker" not in caught.value.failure_metadata.model_dump_json() + str(caught.value) + caplog.text


def test_reference_without_observation_and_first_failure_priority():
    missing_observation = observation_trace()
    missing_observation.observation = None
    with pytest.raises(ExecutionFailure) as caught:
        support.validate_diagnosis(
            extract_output_text(final("diagnosed", [evidence(3, "plans"), evidence(999)])),
            [missing_observation],
        )
    assert caught.value.failure_metadata.failure_reason == "invalid_trace_reference"
    assert caught.value.failure_metadata.field_path == "evidence[0].trace_sequence"


def test_valid_diagnosis_is_preserved_and_one_success_is_enough():
    raw = extract_output_text(final("diagnosed", [evidence(3), evidence(4)]))
    result = support.validate_diagnosis(raw, [
        observation_trace(), observation_trace(4, observation={"ok": False})
    ])
    assert result.model_dump() == json.loads(raw)


@pytest.mark.parametrize("mode", ["agent", "workflow"])
@pytest.mark.parametrize(
    "output,stage,reason",
    [
        (response([], status="incomplete"), "response_status", "not_completed"),
        (response([]), "text_extraction", "empty_output"),
        (text_response("sensitive-marker"), "diagnosis_json", "invalid_json"),
        (final(summary=""), "diagnosis_schema", "validation_error"),
        (final("diagnosed", [evidence(999)]), "evidence_reference", "invalid_trace_reference"),
    ],
)
def test_service_records_failure_once(service_without_db, mode, output, stage, reason, caplog):
    fake = ScriptedResponses([output, final()])
    result = service_without_db(mode, fake)
    assert result.error_code == "invalid_output" and result.diagnosis is None
    finals = [t for t in result.traces if t.event_type == "final_result"]
    assert len(finals) == 1
    assert finals[0].failure_metadata.failure_stage == stage
    assert finals[0].failure_metadata.failure_reason == reason
    assert result.traces[-1].event_type == "execution_failed"
    assert result.traces[-1].error_code == "invalid_output"
    assert all(t.failure_metadata is None for t in result.traces if t is not finals[0])
    assert [t.sequence for t in result.traces] == list(range(1, len(result.traces) + 1))
    assert len(fake.calls) == 1
    assert "sensitive-marker" not in json.dumps([t.model_dump(mode="json") for t in result.traces]) + caplog.text


@pytest.mark.parametrize("mode", ["agent", "workflow"])
def test_success_metadata_is_null_and_requests_unchanged(service_without_db, mode):
    fake = ScriptedResponses([final()])
    result = service_without_db(mode, fake)
    assert result.error_code is None
    assert result.diagnosis.model_dump() == json.loads(extract_output_text(final()))
    assert all(t.failure_metadata is None for t in result.traces)
    assert [t.event_type for t in result.traces[-2:]] == ["final_result", "execution_completed"]
    assert len(fake.calls) == 1
    assert fake.calls[0]["text"]["format"]["schema"] == DiagnosisDTO.model_json_schema()
    assert "failure_metadata" not in json.dumps(fake.calls)
    assert sum(t.event_type == "tool_result" for t in result.traces) == (9 if mode == "workflow" else 0)


def test_legacy_failure_does_not_invent_metadata(service_without_db, monkeypatch):
    def fail(*args, **kwargs):
        raise ExecutionFailure("invalid_output")

    monkeypatch.setattr(support, "run_agent", fail)
    result = service_without_db("agent", ScriptedResponses([]))
    assert result.error_code == "invalid_output"
    assert all(t.failure_metadata is None for t in result.traces)


def test_service_trace_to_evaluation_summary_preserves_metrics(service_without_db, tmp_path):
    from uuid import uuid4
    from app.schemas.support import RequestDTO
    from evaluation.metrics import execution_metrics, load_pricing, summarize
    from evaluation.results import ResultStore
    from tests.test_evaluation_results import make_store

    store = make_store(tmp_path / "fake-run")
    baseline = []
    pricing = load_pricing()
    for execution in store.executions[:2]:
        fake = ScriptedResponses([text_response("sensitive-marker")])
        result = service_without_db(execution.mode, fake)
        request_id = uuid4()
        execution.started = True
        execution.state = "failed"
        execution.execution_success = False
        execution.elapsed_ms = 25
        execution.trace_complete = True
        execution.request_id = str(request_id)
        execution.request = RequestDTO(
            id=request_id, requester_user_id=uid(0x103), organization_id=uid(1),
            content="문의", status="failed", execution_mode=execution.mode,
            created_at=NOW, started_at=NOW, finished_at=NOW,
            diagnosis=None, error_code=result.error_code,
        )
        execution.traces = result.traces
        execution.metrics = execution_metrics(execution, pricing)
        previous = execution.model_copy(deep=True)
        for trace in previous.traces:
            trace.failure_metadata = None
        assert execution_metrics(previous, pricing) == execution.metrics
        baseline.append(previous)
        assert len(fake.calls) == execution.metrics["llm_calls"]["value"] == 1
        assert "failure_metadata" not in json.dumps(fake.calls)
    store.initialize()
    restored = ResultStore.load(store.directory)
    actual = summarize(restored.executions[:2], [])
    previous = summarize(baseline, [])
    for grouping in ("by_mode", "by_case_mode"):
        for key, group in actual[grouping].items():
            detail = group["invalid_output"]
            assert detail["count"] == detail["classified_count"] == 1
            assert sum(r["count"] for r in detail["by_reason"]) == 1
            assert {k: v for k, v in group.items() if k != "invalid_output"} == {
                k: v for k, v in previous[grouping][key].items() if k != "invalid_output"
            }
    for file in store.directory.glob("*.json"):
        assert "sensitive-marker" not in file.read_text()
