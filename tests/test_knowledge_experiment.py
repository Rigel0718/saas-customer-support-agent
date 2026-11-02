import json
from argparse import Namespace
from datetime import timedelta

import pytest

from app.config import Settings
from evaluation import knowledge_answer as experiment
from evaluation.dataset import EvaluationError
from scripts import knowledge_answer as cli
from tests.test_agent_loop import ScriptedResponses
from tests.test_knowledge_answer import answer, cite, text
from tests.test_knowledge_base import AS_OF, kb  # noqa: F401
from tests.test_support_models import NOW

CONFIG = Settings(
    "unused",
    {},
    openai_model="scripted-model",
    agent_max_steps=16,
    agent_max_tool_calls=24,
    agent_timeout_seconds=180,
    workflow_timeout_seconds=180,
    llm_request_timeout_seconds=60,
)


def case(number=2, evidence=("docs/tracing",), **extra):
    return {
        "case_id": f"discussion-{number}",
        "review_rank": 1,
        "created_at": AS_OF.isoformat(),
        "title": "Answer chosen later",
        "claims": [{"id": "C1", "text": "주장", "evidence": list(evidence)}],
        "unscored_parts": [],
        "edited_flag": False,
    } | extra


def cases_file(tmp_path, cases=None, **extra):
    path = tmp_path / "cases.json"
    data = {
        "schema_version": "kb-cases-v1",
        "snapshot_id": "snap-1",
        "status": "confirmed",
        "cases": cases or [case()],
    } | extra
    path.write_text(json.dumps(data))
    return path


def trace(sequence, event_type, seconds, **extra):
    return {
        "sequence": sequence,
        "event_type": event_type,
        "created_at": (NOW + timedelta(seconds=seconds)).isoformat(),
        "step": None,
        "tool_name": None,
        "duration_ms": None,
        "observation": None,
    } | extra


def llm(sequence, seconds, step, duration_ms=1000):
    return trace(sequence, "llm_response", seconds, step=step, duration_ms=duration_ms)


def tool(sequence, seconds, name="get_document", data=None):
    observation = {"ok": True, "data": data or {"path": "docs/tracing"}}
    return trace(
        sequence, "tool_result", seconds, tool_name=name, observation=observation
    )


# --- Case 입력 ---


@pytest.mark.parametrize(
    "extra",
    [
        {"status": "draft"},
        {"cases": [case(), case()]},
        {"cases": [case(claims=[])]},
        {"cases": [case(case_id="issue-2")]},
    ],
)
def test_invalid_cases_file(tmp_path, extra):
    with pytest.raises(EvaluationError, match="invalid_cases_file"):
        experiment.load_cases(cases_file(tmp_path, **extra))


def test_question_comes_from_snapshot_thread_and_is_excluded(session, kb, tmp_path):
    # 선정·검토 기록 필드는 실행에 쓰지 않으므로 허용한다.
    path = cases_file(tmp_path, selection="first 30", scoring={"x": "y"})
    cases = experiment.load_cases(path)
    inputs = experiment.case_inputs(session, cases, ["discussion-2"], "demo")
    item = inputs["discussion-2"]
    assert item.question == "Answer chosen later\n\nexporter question"
    assert item.scope.as_of == AS_OF
    assert item.scope.exclude_thread == ("discussion", 2)
    assert (item.scope.source, item.scope.snapshot_id) == ("demo", "snap-1")


def test_unknown_or_missing_case_threads(session, kb, tmp_path):
    cases = experiment.load_cases(cases_file(tmp_path, cases=[case(), case(999)]))
    with pytest.raises(EvaluationError, match="invalid_cases"):
        experiment.case_inputs(session, cases, ["discussion-1"], "demo")
    with pytest.raises(EvaluationError, match="case_thread_not_in_snapshot"):
        experiment.case_inputs(session, cases, ["discussion-999"], "demo")


def test_mode_order_alternates_by_case_and_repeat():
    plan = experiment.execution_plan(["a", "b"], 2)
    assert [(p["repeat"], p["case_id"], p["mode"]) for p in plan] == [
        (1, "a", "agent"),
        (1, "a", "workflow"),
        (1, "b", "workflow"),
        (1, "b", "agent"),
        (2, "a", "workflow"),
        (2, "a", "agent"),
        (2, "b", "agent"),
        (2, "b", "workflow"),
    ]
    assert [p["order"] for p in plan] == list(range(1, 9))


# --- 사후 환산과 근거 재현율 ---


def test_trace_within_derived_budget_is_not_cut():
    traces = [trace(1, "execution_started", 0), llm(2, 10, 1), tool(3, 11)]
    assert experiment.derived_cut(traces, "agent")["cut_sequence"] is None


@pytest.mark.parametrize(
    "traces, mode, expected",
    [
        (
            [trace(1, "execution_started", 0), llm(2, 30, 1), llm(3, 61, 2)],
            "workflow",
            (3, "execution_timeout", "execution_timeout"),
        ),
        (
            [trace(1, "execution_started", 0), llm(2, 25, 1, duration_ms=20_001)],
            "workflow",
            (2, "llm_request_timeout", "llm_error"),
        ),
        (
            [trace(1, "execution_started", 0)]
            + [llm(i + 2, i, i + 1) for i in range(9)],
            "agent",
            (10, "agent_max_steps", "limit_exceeded"),
        ),
        (
            [trace(1, "execution_started", 0)] + [tool(i + 2, 1) for i in range(17)],
            "agent",
            (18, "agent_max_tool_calls", "limit_exceeded"),
        ),
    ],
)
def test_derived_cut_reasons(traces, mode, expected):
    cut = experiment.derived_cut(traces, mode)
    assert (cut["cut_sequence"], cut["reason"], cut["error_code"]) == expected


def test_step_and_tool_limits_apply_only_to_agent():
    traces = (
        [trace(1, "execution_started", 0)]
        + [tool(i + 2, 1) for i in range(17)]
        + [llm(19, 2, 18)]
    )
    assert experiment.derived_cut(traces, "workflow")["cut_sequence"] is None


def claims(*evidence_lists):
    return [
        experiment.Claim(id=f"C{i}", text="주장", evidence=list(evidence))
        for i, evidence in enumerate(evidence_lists, 1)
    ]


def test_retrieval_and_citation_recall_are_separate():
    traces = [
        tool(
            2,
            1,
            "search_threads",
            {"items": [{"ref": "discussion-3"}, {"ref": "issue-1"}]},
        ),
        tool(3, 2, "get_document", {"path": "docs/tracing"}),
        trace(4, "tool_result", 3, tool_name="get_thread", observation={"ok": False}),
    ]
    answer_dto = {"citations": [{"ref": "docs/tracing"}]}
    recall = experiment.evidence_recall(
        claims(["discussion-3"], ["docs/tracing", "x"], ["missing"]),
        traces,
        answer_dto,
    )
    assert recall["retrieval"] == {"hit_claims": ["C1", "C2"], "claims": 3}
    assert recall["citation"] == {"hit_claims": ["C2"], "claims": 3}


def test_cut_run_fails_and_keeps_only_earlier_retrieval():
    record = {
        "mode": "workflow",
        "status": "completed",
        "error_code": None,
        "traces": [
            trace(1, "execution_started", 0),
            tool(2, 5, "get_document", {"path": "docs/a"}),
            tool(3, 70, "get_document", {"path": "docs/b"}),
            llm(4, 80, 2),
        ],
    }
    result = experiment.condition_result(record, claims(["docs/a"], ["docs/b"]))
    assert (result["status"], result["error_code"]) == ("failed", "execution_timeout")
    assert result["evidence"]["retrieval"]["hit_claims"] == ["C1"]
    assert result["evidence"]["citation"]["hit_claims"] == []


# --- 실행 ---


def first_document_answer(kwargs):
    observations = json.loads(kwargs["input"][0]["content"])["observations"]
    fetched = next(o for o in observations if o["tool_name"] == "get_document")
    observation = fetched["observation"]
    sequence, path = observation["trace_sequence"], observation["data"]["path"]
    return answer([cite(sequence, path, "document")])


def args(tmp_path, **extra):
    values = {
        "cases_path": cases_file(tmp_path),
        "source": "demo",
        "cases": None,
        "repetitions": 1,
        "output_dir": tmp_path / "run",
    }
    return Namespace(**(values | extra))


def test_run_writes_manifest_executions_and_summary(session, kb, tmp_path):
    fake = ScriptedResponses(
        [
            answer([], "insufficient_knowledge"),
            text({"queries": ["tracing"]}),
            first_document_answer,
        ]
    )
    directory = cli.run(args(tmp_path), create=fake, settings=CONFIG, session=session)
    manifest = json.loads((directory / "manifest.json").read_text())
    items = json.loads((directory / "executions.json").read_text())["items"]
    summary = json.loads((directory / "summary.json").read_text())

    assert manifest["status"] == "collected"
    assert manifest["configuration"]["run_budget"]["llm_request_timeout_seconds"] == 60
    assert manifest["configuration"]["derived_budget"] == experiment.DERIVED_BUDGET
    assert manifest["provenance"]["cases"]["sha256"]
    assert [(i["mode"], i["status"]) for i in items] == [
        ("agent", "completed"),
        ("workflow", "completed"),
    ]
    workflow_item = items[1]
    assert workflow_item["evidence"]["citation"]["hit_claims"] == ["C1"]
    assert workflow_item["derived"]["status"] == "completed"
    assert workflow_item["metrics"]["llm_calls"]["value"] == 2
    by_mode = summary["by_mode"]
    assert by_mode["agent"]["run"]["insufficient_knowledge"] == 1
    assert by_mode["workflow"]["run"]["answered"] == 1
    assert by_mode["workflow"]["derived"]["evidence_recall"]["citation"][
        "claim_micro"
    ]["value"] == 1


def test_failed_execution_is_recorded_with_reason(session, kb, tmp_path):
    fake = ScriptedResponses(
        [answer([cite(99, "docs/tracing", "document")]), text({"queries": []})]
    )
    directory = cli.run(
        args(tmp_path),
        create=fake,
        settings=CONFIG,
        session=session,
    )
    items = json.loads((directory / "executions.json").read_text())["items"]
    summary = json.loads((directory / "summary.json").read_text())["by_mode"]
    assert [i["error_code"] for i in items] == ["invalid_output", "invalid_output"]
    assert summary["agent"]["run"]["failure_counts"] == {
        "citation_reference/invalid_citation_reference": 1
    }
    assert summary["workflow"]["run"]["failure_counts"] == {
        "workflow_queries/invalid_queries": 1
    }


def test_main_requires_live_flag_before_any_connection(monkeypatch, capsys):
    monkeypatch.setattr(cli, "run", lambda args: pytest.fail("run called"))
    assert cli.main([]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "error": "live_diagnosis_requires_allow_live_diagnosis"
    }


@pytest.mark.parametrize(
    "flags",
    [
        ["--execution-timeout-seconds", "30"],
        ["--llm-request-timeout-seconds", "10"],
        ["--max-steps", "4"],
        ["--max-tool-calls", "8"],
    ],
)
def test_run_budget_must_cover_derived_budget(monkeypatch, flags):
    monkeypatch.setenv("OPENAI_MODEL", "scripted-model")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with pytest.raises(EvaluationError, match="run_budget_below_derived_budget"):
        cli.experiment_settings(cli.parser().parse_args(flags))


def test_default_experiment_settings(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "scripted-model")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    settings = cli.experiment_settings(cli.parser().parse_args([]))
    assert (
        settings.agent_timeout_seconds,
        settings.workflow_timeout_seconds,
        settings.llm_request_timeout_seconds,
        settings.agent_max_steps,
        settings.agent_max_tool_calls,
    ) == (180, 180, 60, 16, 24)
