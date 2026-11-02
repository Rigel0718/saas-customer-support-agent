"""Knowledge base tools, answer contract, Agent/Workflow answer paths (Fake LLM)."""

import json
from dataclasses import replace

import pytest
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.diagnosis.knowledge_tools import TOOL_DEFINITIONS, execute_knowledge_tool
from app.diagnosis.runtime import ExecutionBudget, ExecutionFailure
from app.schemas.knowledge_answer import KnowledgeAnswerDTO
from app.schemas.support import FailureMetadata, TraceDTO
from app.services import knowledge
from app.services.knowledge_answer import answer_question, validate_answer
from app.workflow import knowledge as workflow
from tests.test_agent_loop import ScriptedResponses, function, response
from tests.test_knowledge_base import AS_OF, kb  # noqa: F401
from tests.test_support_models import NOW

CONFIG = Settings("unused", {}, openai_model="scripted-model")
QUESTION = "How do I configure the OTEL exporter?"


def text(payload):
    return response(
        [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(payload, ensure_ascii=False),
                    }
                ],
            }
        ]
    )


def answer(citations, outcome="answered"):
    return text({"outcome": outcome, "answer": "답변", "citations": citations})


def cite(sequence, ref, source="thread"):
    return {"trace_sequence": sequence, "source": source, "ref": ref}


def call(name, args, call_id):
    return response([function(name, args, call_id)])


def tool(session, scope, name, args, traces=None):
    traces = [] if traces is None else traces
    result = execute_knowledge_tool(
        name,
        json.dumps(args),
        session,
        scope,
        traces,
        ExecutionBudget(60, lambda: 0),
        step=1,
        now=lambda: NOW,
    )
    return result.observation, traces


def ask(session, scope, fake, mode, settings=CONFIG):
    return answer_question(
        session,
        scope,
        QUESTION,
        mode,
        settings,
        create=fake,
        clock=lambda: 0,
        now=lambda: NOW,
    )


def tool_traces(result):
    return [t for t in result.traces if t.event_type == "tool_result"]


# --- Tool ---


@pytest.mark.parametrize(
    "args",
    [
        {"query": "exporter", "limit": 5, "as_of": "2030-01-01T00:00:00Z"},
        {"query": "exporter", "limit": 5, "snapshot_id": "other"},
        {"query": "exporter", "limit": 11},
    ],
)
def test_scope_arguments_are_rejected_before_service(session, kb, monkeypatch, args):
    monkeypatch.setattr(
        knowledge, "search_documents", lambda *a, **k: pytest.fail("service called")
    )
    observation, _ = tool(session, kb, "search_documents", args)
    assert observation["ok"] is False
    assert observation["error_code"] == "invalid_arguments"


@pytest.mark.parametrize("number", [4, 5, 999])
def test_future_excluded_and_missing_threads_are_not_found(session, kb, number):
    observation, traces = tool(
        session, kb, "get_thread", {"kind": "issue", "number": number}
    )
    assert observation == {
        "ok": False,
        "data": None,
        "error_code": "not_found",
        "trace_sequence": 1,
    }
    assert traces[0].error_code == "not_found"


def test_observations_use_refs_without_rank_and_are_traced(session, kb):
    traces = []
    docs, _ = tool(
        session, kb, "search_documents", {"query": "exporter", "limit": 5}, traces
    )
    threads, _ = tool(
        session,
        kb,
        "search_threads",
        {"query": "exporter", "kind": None, "limit": 5},
        traces,
    )
    thread, _ = tool(session, kb, "get_thread", {"kind": "issue", "number": 1}, traces)
    document, _ = tool(session, kb, "get_document", {"path": "docs/tracing"}, traces)
    assert [item["path"] for item in docs["data"]["items"]] == ["docs/tracing"]
    refs = [item["ref"] for item in threads["data"]["items"]]
    assert "issue-1" in refs and "discussion-3" in refs and "issue-4" not in refs
    assert all("rank" not in item for item in threads["data"]["items"])
    assert thread["data"]["ref"] == "issue-1" and thread["data"]["state"] == "open"
    assert [p["body"] for p in thread["data"]["posts"]] == ["earlier reply"]
    assert document["data"]["content"].startswith("Tracing exporter configuration")
    assert [t.tool_name for t in traces] == [
        "search_documents",
        "search_threads",
        "get_thread",
        "get_document",
    ]
    assert [t.observation["trace_sequence"] for t in traces] == [1, 2, 3, 4]
    assert traces[1].arguments == {"query": "exporter", "kind": None, "limit": 5}


def test_database_error_fails_execution(session, kb, monkeypatch):
    def broken(*args, **kwargs):
        raise OperationalError("SELECT", {}, Exception("down"))

    monkeypatch.setattr(knowledge, "search_documents", broken)
    traces = []
    with pytest.raises(OperationalError):
        tool(session, kb, "search_documents", {"query": "x", "limit": 1}, traces)
    assert traces[0].error_code == "database_error" and traces[0].observation is None


def test_tool_schemas_are_strict_without_scope_fields():
    for definition in TOOL_DEFINITIONS:
        parameters = definition["parameters"]
        assert definition["strict"] is True
        assert parameters["additionalProperties"] is False
        assert set(parameters["required"]) == set(parameters["properties"])
        assert not {"as_of", "snapshot_id", "source"} & set(parameters["properties"])


# --- 출력 계약 ---


def observed(session, kb):
    traces = [TraceDTO(sequence=1, event_type="execution_started", created_at=NOW)]
    tool(session, kb, "search_documents", {"query": "exporter", "limit": 5}, traces)
    tool(session, kb, "get_thread", {"kind": "issue", "number": 4}, traces)
    tool(session, kb, "get_thread", {"kind": "issue", "number": 1}, traces)
    return traces


def raw(citations, outcome="answered"):
    return json.dumps({"outcome": outcome, "answer": "답변", "citations": citations})


def failure(session, kb, payload):
    with pytest.raises(ExecutionFailure) as exc:
        validate_answer(payload, observed(session, kb))
    assert exc.value.code == "invalid_output"
    return exc.value.failure_metadata


def test_valid_citations(session, kb):
    result = validate_answer(
        raw([cite(2, "docs/tracing", "document"), cite(4, "issue-1")]),
        observed(session, kb),
    )
    assert [c.ref for c in result.citations] == ["docs/tracing", "issue-1"]


def test_unobserved_citation(session, kb):
    meta = failure(session, kb, raw([cite(2, "docs/self-host", "document")]))
    assert (meta.failure_stage, meta.failure_reason, meta.field_path) == (
        "citation_reference",
        "unobserved_citation",
        "citations[0].ref",
    )


@pytest.mark.parametrize(
    "citation",
    [
        cite(1, "docs/tracing", "document"),  # Tool 관찰이 아님
        cite(3, "issue-4"),  # 실패한 관찰
        cite(4, "issue-1", "document"),  # Tool 종류와 source 불일치
        cite(99, "issue-1"),
    ],
)
def test_invalid_citation_reference(session, kb, citation):
    meta = failure(session, kb, raw([citation]))
    assert (meta.failure_stage, meta.failure_reason) == (
        "citation_reference",
        "invalid_citation_reference",
    )


def test_answered_requires_citation(session, kb):
    meta = failure(session, kb, raw([]))
    assert meta.failure_stage == "answer_schema" and meta.field_path == "citations"


def test_insufficient_knowledge_without_citation(session, kb):
    result = validate_answer(raw([], "insufficient_knowledge"), observed(session, kb))
    assert result.outcome == "insufficient_knowledge"


def test_answer_json_and_schema_failures(session, kb):
    assert failure(session, kb, "{").failure_stage == "answer_json"
    meta = failure(session, kb, raw([{"trace_sequence": 2, "source": "x", "ref": "a"}]))
    assert (meta.failure_stage, meta.field_path) == (
        "answer_schema",
        "citations[0].source",
    )


@pytest.mark.parametrize(
    "stage,reason,ok",
    [
        ("citation_reference", "unobserved_citation", True),
        ("citation_reference", "invalid_citation_reference", True),
        ("workflow_queries", "invalid_queries", True),
        ("answer_json", "invalid_json", True),
        ("answer_schema", "validation_error", True),
        ("workflow_queries", "unobserved_citation", False),
        ("citation_reference", "invalid_json", False),
    ],
)
def test_failure_pairs(stage, reason, ok):
    if ok:
        FailureMetadata(failure_stage=stage, failure_reason=reason)
    else:
        with pytest.raises(ValueError):
            FailureMetadata(failure_stage=stage, failure_reason=reason)


# --- Agent ---


def test_agent_refines_query_and_cites_thread(session, kb):
    fake = ScriptedResponses(
        [
            call("search_threads", {"query": "zzzz", "kind": None, "limit": 5}, "c1"),
            call(
                "search_threads",
                {"query": "exporter", "kind": None, "limit": 5},
                "c2",
            ),
            call("get_thread", {"kind": "issue", "number": 1}, "c3"),
            answer([cite(7, "issue-1")]),
        ]
    )
    result = ask(session, kb, fake, "agent")
    assert result.status == "completed" and result.error_code is None
    assert result.answer.citations[0].ref == "issue-1"
    tools = tool_traces(result)
    assert [t.sequence for t in tools] == [3, 5, 7]
    assert tools[0].observation["data"]["items"] == []
    assert [t.event_type for t in result.traces][-2:] == [
        "final_result",
        "execution_completed",
    ]
    first = fake.calls[0]
    assert first["text"]["format"]["name"] == "knowledge_answer"
    assert first["text"]["format"]["schema"] == KnowledgeAnswerDTO.model_json_schema()
    assert [t["name"] for t in first["tools"]] == [
        "search_documents",
        "get_document",
        "search_threads",
        "get_thread",
    ]
    assert first["parallel_tool_calls"] is False
    assert json.loads(first["input"][0]["content"]) == {
        "as_of": AS_OF.isoformat(),
        "question": QUESTION,
    }


@pytest.mark.parametrize(
    "settings",
    [
        replace(CONFIG, agent_max_steps=3),
        replace(CONFIG, agent_max_tool_calls=2),
    ],
)
def test_agent_limits(session, kb, settings):
    calls = [
        call("search_documents", {"query": "exporter", "limit": 1}, f"c{i}")
        for i in range(5)
    ]
    result = ask(session, kb, ScriptedResponses(calls), "agent", settings)
    assert result.status == "failed" and result.error_code == "limit_exceeded"
    assert result.traces[-1].event_type == "execution_failed"


@pytest.mark.parametrize(
    "ids,reason",
    [([" "], "invalid_call_id"), (["c1", "c1"], "duplicate_call_id")],
)
def test_agent_rejects_bad_call_ids(session, kb, ids, reason):
    calls = [
        function("search_documents", {"query": "exporter", "limit": 1}, call_id)
        for call_id in ids
    ]
    result = ask(session, kb, ScriptedResponses([response(calls)]), "agent")
    assert result.error_code == "invalid_output"
    assert result.failure_metadata.failure_reason == reason
    assert tool_traces(result) == []


def test_agent_invalid_citation_keeps_raw_output(session, kb):
    fake = ScriptedResponses(
        [
            call("search_documents", {"query": "exporter", "limit": 5}, "c1"),
            answer([cite(3, "docs/legacy", "document")]),
        ]
    )
    result = ask(session, kb, fake, "agent")
    assert result.error_code == "invalid_output"
    assert result.failure_metadata.failure_reason == "unobserved_citation"
    assert json.loads(result.raw_output)["citations"][0]["ref"] == "docs/legacy"


# --- Workflow ---


def first_document_answer(kwargs):
    observations = json.loads(kwargs["input"][0]["content"])["observations"]
    fetched = next(o for o in observations if o["tool_name"] == "get_document")
    sequence = fetched["observation"]["trace_sequence"]
    return answer([cite(sequence, fetched["observation"]["data"]["path"], "document")])


def run_workflow(session, kb, queries):
    fake = ScriptedResponses([text({"queries": queries}), first_document_answer])
    return ask(session, kb, fake, "workflow"), fake


def test_workflow_is_deterministic_with_fixed_retrieval(session, kb):
    first, fake = run_workflow(session, kb, ["exporter", "tracing"])
    second, _ = run_workflow(session, kb, ["exporter", "tracing"])
    assert first.status == second.status == "completed"
    assert len(fake.calls) == 2
    assert fake.calls[0]["text"]["format"]["name"] == "knowledge_queries"
    assert "tools" not in fake.calls[0] and "tools" not in fake.calls[1]
    calls = [(t.tool_name, t.arguments) for t in tool_traces(first)]
    assert calls == [(t.tool_name, t.arguments) for t in tool_traces(second)]
    assert calls[:4] == [
        ("search_documents", {"query": "exporter", "limit": 5}),
        ("search_threads", {"query": "exporter", "kind": None, "limit": 5}),
        ("search_documents", {"query": "tracing", "limit": 5}),
        ("search_threads", {"query": "tracing", "kind": None, "limit": 5}),
    ]
    documents = [a["path"] for n, a in calls if n == "get_document"]
    threads = [(a["kind"], a["number"]) for n, a in calls if n == "get_thread"]
    assert documents == ["docs/tracing"]
    assert len(threads) == 3 and len(set(threads)) == 3
    observations = json.loads(fake.calls[1]["input"][0]["content"])["observations"]
    assert [o["observation"] for o in observations] == [
        t.observation for t in tool_traces(first)
    ]


def test_interleave_order():
    assert workflow.interleave([["a", "b", "c"], ["b", "d"]], 3) == ["a", "b", "d"]
    assert workflow.interleave([[], ["x"]], 3) == ["x"]


@pytest.mark.parametrize("queries", [[], ["a", "b", "c", "d"], [" "], ["x" * 301]])
def test_invalid_queries_stop_before_tools(session, kb, queries):
    result, _ = run_workflow(session, kb, queries)
    assert result.error_code == "invalid_output"
    meta = result.failure_metadata
    assert (meta.failure_stage, meta.failure_reason) == (
        "workflow_queries",
        "invalid_queries",
    )
    assert tool_traces(result) == []


def test_function_call_in_answer_step(session, kb):
    fake = ScriptedResponses(
        [
            text({"queries": ["exporter"]}),
            call("get_document", {"path": "docs/tracing"}, "c1"),
        ]
    )
    result = ask(session, kb, fake, "workflow")
    assert result.failure_metadata.failure_reason == "unexpected_function_call"


# --- 공통 진입점 ---


def test_both_modes_share_contract(session, kb):
    agent = ask(
        session,
        kb,
        ScriptedResponses([answer([], "insufficient_knowledge")]),
        "agent",
    )
    flow = ask(
        session,
        kb,
        ScriptedResponses(
            [text({"queries": ["exporter"]}), answer([], "insufficient_knowledge")]
        ),
        "workflow",
    )
    for result in (agent, flow):
        assert result.status == "completed"
        assert result.answer.outcome == "insufficient_knowledge"
        assert result.traces[0].event_type == "execution_started"
        assert result.traces[-1].event_type == "execution_completed"


def test_llm_error_and_timeout_are_classified(session, kb):
    def boom(**kwargs):
        raise RuntimeError("provider down")

    result = ask(session, kb, boom, "workflow")
    assert result.error_code == "llm_error"
    ticks = iter([0, 0, 0, 200, 200, 200, 200])
    timed = answer_question(
        session,
        kb,
        QUESTION,
        "agent",
        CONFIG,
        create=ScriptedResponses([answer([], "insufficient_knowledge")]),
        clock=lambda: next(ticks, 200),
        now=lambda: NOW,
    )
    assert timed.error_code == "execution_timeout"


def test_entry_point_rejects_bad_input(session, kb):
    with pytest.raises(ValueError):
        answer_question(session, kb, " ", "agent", CONFIG)
    with pytest.raises(ValueError):
        answer_question(session, kb, QUESTION, "hybrid", CONFIG)
    with pytest.raises(knowledge.KnowledgeError):
        answer_question(session, object(), QUESTION, "agent", CONFIG)
