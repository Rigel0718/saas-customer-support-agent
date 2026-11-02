"""Knowledge answer experiment: golden-set cases, collection, post-hoc budget.

Content correctness is not graded here; human labels come later.
"""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Literal

from pydantic import ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.diagnosis.knowledge_tools import observed_refs
from app.models.knowledge import KnowledgeThread
from app.services.knowledge import KnowledgeScope
from app.services.knowledge_answer import answer_question
from app.workflow import knowledge as workflow
from evaluation.dataset import EvaluationError, Model, Nonempty, json_snapshot
from evaluation.fixtures import utc_now
from evaluation.metrics import measure, numeric_statistics, ratio, trace_metrics
from evaluation.results import atomic_json, code_provenance

SCHEMA_VERSION = "knowledge-answer-v1"
CASES_PATH = Path(".evaluation-results/kb-cases-v1/cases.json")
SOURCE = "langfuse"
RUN_PREFIX = "knowledge"
CONDITIONS = ("run", "derived")
# 기본 운영 예산. 넉넉한 예산으로 실행한 Trace를 이 예산 시점에서 잘라 환산한다.
DERIVED_BUDGET = {
    "execution_timeout_seconds": 60,
    "agent_max_steps": 8,
    "agent_max_tool_calls": 16,
    "llm_request_timeout_seconds": 20,
}
NUMERIC_METRICS = (
    "latency_ms",
    "llm_calls",
    "tool_calls",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "estimated_cost_usd",
)
LIMITATIONS = [
    "29 cases and few repetitions do not establish general superiority "
    "or statistical significance.",
    "Content correctness is not graded; it requires human review of executions.json.",
    "Evidence lists are not exhaustive, so evidence recall is a supporting metric.",
    "Derived budget results cut the large-budget trace; a call near the derived "
    "deadline could have ended differently under min(request timeout, remaining).",
]


class Claim(Model):
    id: Nonempty
    text: Nonempty
    evidence: Annotated[list[Nonempty], Field(min_length=1)]


class KnowledgeCase(Model):
    case_id: Annotated[str, Field(pattern=r"^discussion-[1-9][0-9]*$")]
    review_rank: int
    created_at: datetime
    title: Nonempty
    claims: Annotated[list[Claim], Field(min_length=1)]
    unscored_parts: list[str]
    edited_flag: bool


class KnowledgeCaseSet(Model):
    # 선정·검토 기록 필드(selection, scoring 등)는 실행에 쓰지 않으므로 무시한다.
    model_config = ConfigDict(extra="ignore", strict=True)

    schema_version: Nonempty
    snapshot_id: Nonempty
    status: Literal["confirmed"]
    cases: Annotated[list[KnowledgeCase], Field(min_length=1)]


@dataclass(frozen=True)
class CaseInput:
    case: KnowledgeCase
    scope: KnowledgeScope
    question: str


def load_cases(path: Path = CASES_PATH) -> KnowledgeCaseSet:
    try:
        cases = KnowledgeCaseSet.model_validate_json(path.read_bytes())
    except Exception:
        raise EvaluationError("invalid_cases_file") from None
    ids = [case.case_id for case in cases.cases]
    if len(set(ids)) != len(ids):
        raise EvaluationError("invalid_cases_file")
    return cases


def thread_number(case: KnowledgeCase) -> int:
    return int(case.case_id.split("-", 1)[1])


def case_inputs(
    session: Session, cases: KnowledgeCaseSet, case_ids: list[str], source: str = SOURCE
) -> dict[str, CaseInput]:
    """질문은 스냅샷의 원 스레드(제목+본문)에서 읽고, 그 스레드는 검색 범위에서 뺀다."""
    by_id = {case.case_id: case for case in cases.cases}
    if not case_ids or any(case_id not in by_id for case_id in case_ids):
        raise EvaluationError("invalid_cases")
    inputs = {}
    for case_id in case_ids:
        case = by_id[case_id]
        thread = session.scalar(
            select(KnowledgeThread).where(
                KnowledgeThread.source == source,
                KnowledgeThread.snapshot_id == cases.snapshot_id,
                KnowledgeThread.kind == "discussion",
                KnowledgeThread.number == thread_number(case),
            )
        )
        if thread is None:
            raise EvaluationError("case_thread_not_in_snapshot")
        inputs[case_id] = CaseInput(
            case,
            KnowledgeScope(
                source=source,
                snapshot_id=cases.snapshot_id,
                as_of=case.created_at,
                exclude_thread=("discussion", thread_number(case)),
            ),
            f"{thread.title}\n\n{thread.body}",
        )
    return inputs


def execution_plan(case_ids: list[str], repetitions: int) -> list[dict]:
    plan = []
    for repeat in range(1, repetitions + 1):
        for index, case_id in enumerate(case_ids):
            # Case마다 모드 순서를 바꿔 시간대 효과가 한 모드에 몰리지 않게 한다.
            modes = (
                ("agent", "workflow")
                if (index + repeat) % 2
                else ("workflow", "agent")
            )
            for mode in modes:
                plan.append(
                    {
                        "order": len(plan) + 1,
                        "repeat": repeat,
                        "case_id": case_id,
                        "mode": mode,
                    }
                )
    return plan


def derived_cut(traces: list[dict], mode: str, budget: dict = DERIVED_BUDGET) -> dict:
    """작은 예산이었다면 끊겼을 첫 Trace를 찾는다. 없으면 실행 결과를 그대로 쓴다."""
    started = datetime.fromisoformat(traces[0]["created_at"])
    tool_calls = 0
    for trace in traces:
        elapsed = datetime.fromisoformat(trace["created_at"]) - started
        reason = None
        if elapsed.total_seconds() > budget["execution_timeout_seconds"]:
            reason, code = "execution_timeout", "execution_timeout"
        elif trace["event_type"] == "llm_response":
            request_ms = budget["llm_request_timeout_seconds"] * 1000
            if (trace["duration_ms"] or 0) > request_ms:
                reason, code = "llm_request_timeout", "llm_error"
            elif mode == "agent" and trace["step"] > budget["agent_max_steps"]:
                reason, code = "agent_max_steps", "limit_exceeded"
        elif trace["event_type"] == "tool_result" and mode == "agent":
            tool_calls += 1
            if tool_calls > budget["agent_max_tool_calls"]:
                reason, code = "agent_max_tool_calls", "limit_exceeded"
        if reason:
            return {
                "cut_sequence": trace["sequence"],
                "reason": reason,
                "error_code": code,
            }
    return {"cut_sequence": None, "reason": None, "error_code": None}


def evidence_recall(
    claims: list[Claim], traces: list[dict], answer: dict | None
) -> dict:
    """조회 기준(관찰에 나온 문서·스레드)과 인용 기준(최종 답변 인용)의 주장 단위 적중."""
    retrieved = set()
    for trace in traces:
        observation = trace.get("observation") or {}
        if trace["event_type"] == "tool_result" and observation.get("ok"):
            retrieved |= observed_refs(trace["tool_name"], observation["data"])
    cited = {c["ref"] for c in (answer or {}).get("citations", [])}
    result = {}
    for basis, refs in (("retrieval", retrieved), ("citation", cited)):
        hits = [claim.id for claim in claims if set(claim.evidence) & refs]
        result[basis] = {"hit_claims": hits, "claims": len(claims)}
    return result


def condition_result(record: dict, claims: list[Claim]) -> dict:
    """Trace를 작은 예산 시점에서 잘라 상태와 근거 재현율을 다시 계산한다."""
    cut = derived_cut(record["traces"], record["mode"])
    if cut["cut_sequence"] is None:
        return {
            "status": record["status"],
            "error_code": record["error_code"],
            "cut": cut,
            "evidence": record["evidence"],
        }
    kept = [t for t in record["traces"] if t["sequence"] < cut["cut_sequence"]]
    return {
        "status": "failed",
        "error_code": cut["error_code"],
        "cut": cut,
        "evidence": evidence_recall(claims, kept, None),
    }


def collect(
    session: Session,
    inputs: dict[str, CaseInput],
    plan: list[dict],
    settings: Settings,
    pricing: dict,
    *,
    create=None,
    clock: Callable = time.monotonic,
    now: Callable = utc_now,
    on_record: Callable[[list[dict]], None] | None = None,
) -> list[dict]:
    results = []
    for item in plan:
        case_input = inputs[item["case_id"]]
        before = clock()
        result = answer_question(
            session,
            case_input.scope,
            case_input.question,
            item["mode"],
            settings,
            create=create,
            now=now,
        )
        elapsed_ms = max(0.0, (clock() - before) * 1000)
        traces = [trace.model_dump(mode="json") for trace in result.traces]
        answer = result.answer.model_dump(mode="json") if result.answer else None
        record = dict(
            item,
            status=result.status,
            error_code=result.error_code,
            failure_metadata=(
                result.failure_metadata.model_dump(mode="json")
                if result.failure_metadata
                else None
            ),
            answer=answer,
            raw_output=result.raw_output,
            traces=traces,
            elapsed_ms=elapsed_ms,
            metrics=trace_metrics(traces, True, pricing),
            evidence=evidence_recall(case_input.case.claims, traces, answer),
        )
        record["metrics"]["latency_ms"] = measure(elapsed_ms, True)
        record["derived"] = condition_result(record, case_input.case.claims)
        results.append(record)
        # 중단되어도 끝난 실행을 잃지 않도록 실행마다 저장할 수 있게 한다.
        if on_record is not None:
            on_record(results)
    return results


def next_run_name(root: Path, execution_count: int) -> str:
    prefix = f"{RUN_PREFIX}-{execution_count}-v"
    pattern = re.compile(re.escape(prefix) + r"(\d+)$")
    versions = [
        int(match.group(1))
        for path in (root.iterdir() if root.exists() else [])
        if (match := pattern.match(path.name))
    ]
    return f"{prefix}{max(versions, default=0) + 1}"


def build_manifest(
    *,
    run_name: str,
    started_at: datetime,
    source: str,
    cases_path: Path,
    snapshot_id: str,
    case_ids: list[str],
    repetitions: int,
    plan: list[dict],
    settings: Settings,
    pricing: dict,
) -> dict:
    # Settings나 환경변수 전체가 아니라 비교 조건만 허용 목록으로 기록한다.
    return {
        "schema_version": SCHEMA_VERSION,
        "run_name": run_name,
        "started_at": started_at.isoformat(),
        "finished_at": None,
        "status": "collecting",
        "source": source,
        "snapshot_id": snapshot_id,
        "cases": case_ids,
        "repetitions": repetitions,
        "plan": plan,
        "question_format": "snapshot thread title + blank line + body",
        "scope": "as_of = case created_at, case thread excluded",
        "configuration": {
            "model": settings.openai_model,
            "run_budget": {
                "execution_timeout_seconds": settings.agent_timeout_seconds,
                "agent_max_steps": settings.agent_max_steps,
                "agent_max_tool_calls": settings.agent_max_tool_calls,
                "llm_request_timeout_seconds": settings.llm_request_timeout_seconds,
            },
            "derived_budget": dict(DERIVED_BUDGET),
            "workflow": {
                "max_queries": workflow.MAX_QUERIES,
                "search_limit": workflow.SEARCH_LIMIT,
                "fetch_documents": workflow.FETCH_DOCUMENTS,
                "fetch_threads": workflow.FETCH_THREADS,
                "llm_calls": 2,
            },
            "mode_order": "alternates by case index and repeat",
        },
        "evidence_recall": {
            "retrieval": "claim hit if any evidence ref is in an ok tool observation",
            "citation": "claim hit if any evidence ref is cited in a validated answer",
        },
        "pricing_version": pricing.get("pricing_version"),
        "provenance": {
            "code": code_provenance(),
            "prompt_files": [
                "app/agent/knowledge.py",
                "app/workflow/knowledge.py",
                "app/diagnosis/knowledge_tools.py",
            ],
            "cases": json_snapshot(cases_path),
        },
        "grading": "not_graded",
        "comparison_limitations": list(LIMITATIONS),
        "latency_boundary": "monotonic: answer_question call only",
    }


def summarize(executions: list[dict]) -> dict:
    def condition(items: list[dict], name: str) -> dict:
        results = [e if name == "run" else e["derived"] for e in items]
        errors, reasons = {}, {}
        for result in results:
            if result["error_code"]:
                errors[result["error_code"]] = errors.get(result["error_code"], 0) + 1
        if name == "run":
            for e in items:
                meta = e["failure_metadata"]
                if meta:
                    key = f"{meta['failure_stage']}/{meta['failure_reason']}"
                    reasons[key] = reasons.get(key, 0) + 1
        else:
            for e in items:
                reason = e["derived"]["cut"]["reason"]
                if reason:
                    reasons[reason] = reasons.get(reason, 0) + 1
        recall = {}
        for basis in ("retrieval", "citation"):
            hits = sum(len(r["evidence"][basis]["hit_claims"]) for r in results)
            claims = sum(r["evidence"][basis]["claims"] for r in results)
            recall[basis] = {
                "claim_micro": ratio(hits, claims),
                "case_macro": (
                    sum(
                        len(r["evidence"][basis]["hit_claims"])
                        / r["evidence"][basis]["claims"]
                        for r in results
                    )
                    / len(results)
                    if results
                    else None
                ),
            }
        return {
            "executions": len(results),
            "completed": sum(r["status"] == "completed" for r in results),
            "failed": sum(r["status"] == "failed" for r in results),
            "answered": sum(
                r["status"] == "completed" and e["answer"]["outcome"] == "answered"
                for r, e in zip(results, items)
            ),
            "insufficient_knowledge": sum(
                r["status"] == "completed"
                and e["answer"]["outcome"] == "insufficient_knowledge"
                for r, e in zip(results, items)
            ),
            "error_counts": dict(sorted(errors.items())),
            "failure_counts": dict(sorted(reasons.items())),
            "evidence_recall": recall,
        }

    def group(items: list[dict]) -> dict:
        wrapped = [SimpleNamespace(metrics=e["metrics"], censored=False) for e in items]
        return {
            **{name: condition(items, name) for name in CONDITIONS},
            "metrics": {
                metric: numeric_statistics(wrapped, metric)
                for metric in NUMERIC_METRICS
            },
        }

    def grouped(key) -> dict:
        groups: dict[str, list[dict]] = {}
        for execution in executions:
            groups.setdefault(key(execution), []).append(execution)
        return {name: group(items) for name, items in groups.items()}

    return {
        "schema_version": SCHEMA_VERSION,
        "grading": "not_graded",
        "by_mode": grouped(lambda e: e["mode"]),
        "by_case_mode": grouped(lambda e: f"{e['case_id']}/{e['mode']}"),
    }


def save_run(directory: Path, manifest: dict, executions: list[dict]) -> None:
    envelope = {"schema_version": SCHEMA_VERSION, "run_name": manifest["run_name"]}
    atomic_json(directory / "manifest.json", manifest)
    atomic_json(directory / "executions.json", {**envelope, "items": executions})
    atomic_json(directory / "summary.json", {**envelope, **summarize(executions)})
