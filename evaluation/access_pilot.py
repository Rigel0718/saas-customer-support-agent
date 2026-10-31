"""Access-loss pilot: fixed cases, deterministic noise, sequential collection. No grading."""

import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated
from uuid import UUID, uuid5

from pydantic import Field
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AuditEvent, Organization, Payment, Subscription, User
from app.schemas.support import RequestCreate
from app.services import customer, support
from app.services.policy import load_policies
from app.workflow.runner import AUDIT_PREFETCH_LIMIT, AUDIT_WINDOW
from evaluation.dataset import (
    DATA_DIR,
    EvaluationError,
    GroundTruth,
    Model,
    Nonempty,
    json_snapshot,
    policy_snapshot,
)
from evaluation.fixtures import record_id, utc_now
from evaluation.metrics import measure, numeric_statistics, trace_metrics
from evaluation.results import atomic_json, code_provenance

SCHEMA_VERSION = "access-pilot-v1"
GT_PATH = DATA_DIR / "access_gt.json"
CAUSES = {
    "role_demoted",
    "subscription_past_due",
    "member_limit_reached",
    "confirmed_service_outage",
    "other_user_role_change",
    "old_role_change",
}
RUN_PREFIX = "access"
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
    "Pilot cases and few repetitions do not establish general superiority or statistical significance.",
    "Diagnoses are not graded; correctness requires human review of executions.json.",
    "Agent and Workflow differ in model calls, context size and retrieval scope; dynamic tool selection is not isolated causally.",
]

CONTENT = (
    "어제까지 되던 멤버 관리 기능을 오늘부터 사용할 수 없습니다. "
    "무엇이 바뀌었는지 확인해 주세요."
)
EXTRA_MEMBERS = 20
NOISE_WINDOW_SECONDS = 30 * 24 * 3600
NOISE_KINDS = (
    "user_login",
    "document_viewed",
    "document_edited",
    "member_invited",
    "role_changed",
)
NOISE_WEIGHTS = (40, 30, 20, 5, 5)


@dataclass(frozen=True)
class PilotCase:
    case_id: str
    requester_role: str
    subscription_status: str
    demoted: bool
    suspended: bool
    old_promotion: bool


CASES = {
    case.case_id: case
    for case in (
        # 관리자 B가 요청자를 admin -> member로 강등했다.
        PilotCase("A01", "member", "active", True, False, False),
        # 결제 실패 후 system이 구독을 active -> past_due로 바꿨다.
        PilotCase("A02", "admin", "past_due", False, True, False),
        PilotCase("A03", "member", "past_due", True, True, False),
        # 관련 변경 없음. 5일 전 member -> admin 승격만 있다(두 모드의 조회 범위 안).
        # 오래된 변경을 오늘 문제의 원인으로 오인하는지 확인하는 함정이다.
        PilotCase("A04", "admin", "active", False, False, True),
    )
}


class AccessGroundTruthSet(Model):
    gt_version: Nonempty
    labeling_rules: Annotated[list[Nonempty], Field(min_length=1)]
    cases: list[GroundTruth]


def load_ground_truth(path: Path = GT_PATH) -> AccessGroundTruthSet:
    try:
        gt = AccessGroundTruthSet.model_validate_json(path.read_bytes())
    except Exception:
        raise EvaluationError("invalid_ground_truth") from None
    policy_ids = {document.document_id for document in load_policies()}
    valid = [truth.case_id for truth in gt.cases] == list(CASES) and all(
        set(truth.primary_causes) | set(truth.excluded_causes) <= CAUSES
        and not set(truth.primary_causes) & set(truth.excluded_causes)
        and set(truth.policy_document_ids) <= policy_ids
        # 원인이 있으면 diagnosed, 없으면 needs_information이다.
        and (truth.expected_outcome == "diagnosed") == bool(truth.primary_causes)
        for truth in gt.cases
    )
    if not valid:
        raise EvaluationError("invalid_ground_truth")
    return gt


def scope(case_id: str, noise: int) -> str:
    return f"{case_id}/n{noise}"


def user_ids(run_id: UUID, case_id: str, noise: int) -> dict[str, UUID]:
    keys = ["requester", "owner", "admin_b"] + [
        f"m{number:02d}" for number in range(1, EXTRA_MEMBERS + 1)
    ]
    return {key: record_id(run_id, scope(case_id, noise), key) for key in keys}


def noise_events(
    *,
    seed: int,
    count: int,
    case_id: str,
    organization_id: UUID,
    users: dict[str, UUID],
    reference_time: datetime,
) -> tuple[list[AuditEvent], dict[UUID, str]]:
    """Return noise events and the resulting roles of extra members.

    Noise never targets the requester and never changes the subscription.
    """
    rng = random.Random(f"{seed}:{case_id}:{count}")
    members = [users[key] for key in users if key.startswith("m")]
    admins = [users["owner"], users["admin_b"]]
    everyone = list(users.values())
    events = []
    for number in range(count):
        kind = rng.choices(NOISE_KINDS, NOISE_WEIGHTS)[0]
        occurred_at = reference_time - timedelta(
            seconds=rng.randrange(1, NOISE_WINDOW_SECONDS)
        )
        actor, target, payload = rng.choice(everyone), None, {}
        if kind.startswith("document_"):
            payload = {"document_id": f"doc-{rng.randrange(1, 200)}"}
        elif kind == "member_invited":
            actor, payload = rng.choice(admins), {"email_domain": "example.com"}
        elif kind == "role_changed":
            actor, target = rng.choice(admins), rng.choice(members)
        events.append(
            AuditEvent(
                id=uuid5(organization_id, f"noise:{seed}:{number}"),
                organization_id=organization_id,
                occurred_at=occurred_at,
                actor_user_id=actor,
                event_type=kind,
                target_user_id=target,
                payload=payload,
            )
        )
    # 역할 변경 노이즈는 시간순으로 이어져 현재 역할과 모순되지 않게 한다.
    roles = {member: "member" for member in members}
    for event in sorted(
        (e for e in events if e.event_type == "role_changed"),
        key=lambda e: (e.occurred_at, e.id),
    ):
        before = roles[event.target_user_id]
        after = "admin" if before == "member" else "member"
        event.payload = {"from_role": before, "to_role": after}
        roles[event.target_user_id] = after
    return events, roles


def cause_events(
    case: PilotCase,
    organization_id: UUID,
    users: dict[str, UUID],
    reference_time: datetime,
) -> list[AuditEvent]:
    def event(key, hours_ago, actor, kind, target, payload):
        return AuditEvent(
            id=uuid5(organization_id, f"cause:{key}"),
            organization_id=organization_id,
            occurred_at=reference_time - timedelta(hours=hours_ago),
            actor_user_id=actor,
            event_type=kind,
            target_user_id=target,
            payload=payload,
        )

    events = []
    if case.demoted:
        events.append(
            event(
                "demoted",
                20,
                users["admin_b"],
                "role_changed",
                users["requester"],
                {"from_role": "admin", "to_role": "member"},
            )
        )
    if case.suspended:
        events.append(
            event(
                "suspended",
                22,
                None,
                "subscription_status_changed",
                None,
                {
                    "from_status": "active",
                    "to_status": "past_due",
                    "reason": "payment_failed",
                },
            )
        )
    if case.old_promotion:
        events.append(
            event(
                "old_promotion",
                5 * 24,
                users["owner"],
                "role_changed",
                users["requester"],
                {"from_role": "member", "to_role": "admin"},
            )
        )
    return events


def case_rows(
    run_id: UUID,
    case_id: str,
    noise: int,
    seed: int,
    reference_time: datetime,
) -> list:
    case = CASES[case_id]
    organization_id = record_id(run_id, scope(case_id, noise), "organization")
    users = user_ids(run_id, case_id, noise)
    noise_rows, member_roles = noise_events(
        seed=seed,
        count=noise,
        case_id=case_id,
        organization_id=organization_id,
        users=users,
        reference_time=reference_time,
    )
    roles = {
        users["requester"]: case.requester_role,
        users["owner"]: "owner",
        users["admin_b"]: "admin",
        **member_roles,
    }
    payments = [
        Payment(
            id=record_id(run_id, scope(case_id, noise), "payment-succeeded"),
            organization_id=organization_id,
            status="succeeded",
            paid_at=reference_time - timedelta(days=31),
            created_at=reference_time - timedelta(days=31),
        )
    ]
    if case.suspended:
        payments.append(
            Payment(
                id=record_id(run_id, scope(case_id, noise), "payment-failed"),
                organization_id=organization_id,
                status="failed",
                paid_at=None,
                created_at=reference_time - timedelta(hours=22, minutes=5),
            )
        )
    return [
        Organization(
            id=organization_id,
            name=f"Pilot {case_id}",
            member_limit=len(users) + 5,
        ),
        *(
            User(id=user_id, organization_id=organization_id, role=role)
            for user_id, role in roles.items()
        ),
        Subscription(
            id=record_id(run_id, scope(case_id, noise), "subscription"),
            organization_id=organization_id,
            plan="pro",
            status=case.subscription_status,
            expires_at=reference_time + timedelta(days=30),
        ),
        *payments,
        *cause_events(case, organization_id, users, reference_time),
        *noise_rows,
    ]


def insert_fixtures(
    engine: Engine,
    run_id: UUID,
    case_ids: list[str],
    noise_levels: list[int],
    seed: int,
    reference_time: datetime,
) -> None:
    with Session(engine) as session:
        for noise in noise_levels:
            for case_id in case_ids:
                rows = case_rows(run_id, case_id, noise, seed, reference_time)
                # FK 순서를 지키기 위해 조직을 먼저 저장한다.
                session.add(rows[0])
                session.flush()
                session.add_all(rows[1:])
                session.flush()
        session.commit()


def execution_plan(
    case_ids: list[str], noise_levels: list[int], repetitions: int
) -> list[dict]:
    plan = []
    for repeat in range(1, repetitions + 1):
        modes = ("agent", "workflow") if repeat % 2 else ("workflow", "agent")
        for noise in noise_levels:
            for case_id in case_ids:
                for mode in modes:
                    plan.append(
                        {
                            "order": len(plan) + 1,
                            "repeat": repeat,
                            "noise": noise,
                            "case_id": case_id,
                            "mode": mode,
                        }
                    )
    return plan


def audit_prefetch(traces: list[dict]) -> dict | None:
    for trace in traces:
        observation = trace.get("observation") or {}
        if trace["tool_name"] == "get_audit_events" and trace["call_id"] is None:
            data = observation.get("data") or {}
            return {
                "returned": len(data.get("items", [])),
                "total_count": data.get("total_count"),
                "truncated": data.get("truncated"),
            }
    return None


def collect(
    engine: Engine,
    run_id: UUID,
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
        record = dict(item, request_id=None, error=None, request=None, traces=[])
        requester = user_ids(run_id, item["case_id"], item["noise"])["requester"]
        before = clock()
        try:
            with Session(engine) as session:
                context = customer.authenticate(session, requester)
                request = support.create_request(
                    session, context, RequestCreate(content=CONTENT)
                )
                record["request_id"] = str(request.id)
                support.diagnose_request(
                    session,
                    context,
                    request.id,
                    settings,
                    create=create,
                    now=now,
                    mode=item["mode"],
                )
        except Exception as exc:
            record["error"] = (
                "database_error" if isinstance(exc, SQLAlchemyError) else "service_error"
            )
        record["elapsed_ms"] = max(0.0, (clock() - before) * 1000)
        if record["request_id"]:
            with Session(engine) as session:
                context = customer.authenticate(session, requester)
                request_id = UUID(record["request_id"])
                request = support.get_request(session, context, request_id)
                record["request"] = support.request_dto(session, request).model_dump(
                    mode="json"
                )
                record["traces"] = [
                    trace.model_dump(mode="json")
                    for trace in support.get_traces(session, context, request_id).items
                ]
        complete = bool(record["request"]) and record["request"]["status"] in (
            "completed",
            "failed",
        )
        record["metrics"] = trace_metrics(record["traces"], complete, pricing)
        record["metrics"]["latency_ms"] = measure(
            record["elapsed_ms"], record["request_id"] is not None
        )
        record["audit_prefetch"] = audit_prefetch(record["traces"])
        results.append(record)
        # 중단되어도 끝난 실행을 잃지 않도록 실행마다 저장할 수 있게 한다.
        if on_record is not None:
            on_record(results)
    return results


def next_run_name(root: Path, execution_count: int) -> str:
    """invite-50-v1과 같은 규칙으로 access-<실행 수>-v<버전>을 정한다."""
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
    run_id: UUID,
    run_name: str,
    reference_time: datetime,
    db_schema: str,
    seed: int,
    noise_levels: list[int],
    case_ids: list[str],
    repetitions: int,
    plan: list[dict],
    settings: Settings,
    pricing: dict,
) -> dict:
    # Settings나 환경변수 전체가 아니라 비교 조건만 허용 목록으로 기록한다.
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": str(run_id),
        "run_name": run_name,
        "reference_time": reference_time.isoformat(),
        "started_at": reference_time.isoformat(),
        "finished_at": None,
        "status": "collecting",
        "db_schema": db_schema,
        "seed": seed,
        "noise_levels": noise_levels,
        "cases": case_ids,
        "repetitions": repetitions,
        "content": CONTENT,
        "plan": plan,
        "configuration": {
            "diagnosis_model": settings.openai_model,
            "agent_max_steps": settings.agent_max_steps,
            "agent_max_tool_calls": settings.agent_max_tool_calls,
            "agent_timeout_seconds": settings.agent_timeout_seconds,
            "workflow_timeout_seconds": settings.workflow_timeout_seconds,
            "llm_request_timeout_seconds": settings.llm_request_timeout_seconds,
            "workflow_tool_calls": 9,
            "workflow_llm_calls": 1,
            "workflow_audit_window_days": AUDIT_WINDOW.days,
            "workflow_audit_limit": AUDIT_PREFETCH_LIMIT,
            "agent_audit_limit_max": 50,
            "noise_window_days": NOISE_WINDOW_SECONDS // 86400,
            "extra_members": EXTRA_MEMBERS,
            "mode_order": "odd: agent/workflow; even: workflow/agent",
        },
        "pricing_version": pricing.get("pricing_version"),
        "provenance": {
            "code": code_provenance(),
            "policies": policy_snapshot(),
            "gt": json_snapshot(GT_PATH),
        },
        "grading": "not_graded",
        "comparison_limitations": list(LIMITATIONS),
        "latency_boundary": (
            "monotonic: immediately before create_request through "
            "diagnose_request return/exception; includes service persistence"
        ),
    }


def summarize(executions: list[dict]) -> dict:
    def group(items: list[dict]) -> dict:
        requests = [e["request"] or {} for e in items]
        outcomes, errors = {}, {}
        for request in requests:
            diagnosis = request.get("diagnosis") or {}
            if diagnosis.get("outcome"):
                outcomes[diagnosis["outcome"]] = outcomes.get(diagnosis["outcome"], 0) + 1
            if request.get("error_code"):
                errors[request["error_code"]] = errors.get(request["error_code"], 0) + 1
        wrapped = [SimpleNamespace(metrics=e["metrics"], censored=False) for e in items]
        return {
            "started": sum(e["request_id"] is not None for e in items),
            "completed": sum(r.get("status") == "completed" for r in requests),
            "failed": sum(r.get("status") == "failed" for r in requests),
            "environment_errors": sum(e["error"] is not None for e in items),
            "outcome_counts": dict(sorted(outcomes.items())),
            "error_counts": dict(sorted(errors.items())),
            "metrics": {
                metric: numeric_statistics(wrapped, metric) for metric in NUMERIC_METRICS
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
        "by_mode_noise": grouped(lambda e: f"{e['mode']}/n{e['noise']}"),
        "by_case_mode_noise": grouped(
            lambda e: f"{e['case_id']}/{e['mode']}/n{e['noise']}"
        ),
    }


def save_run(directory: Path, manifest: dict, executions: list[dict]) -> None:
    envelope = {"schema_version": SCHEMA_VERSION, "run_id": manifest["run_id"]}
    atomic_json(directory / "manifest.json", manifest)
    atomic_json(directory / "executions.json", {**envelope, "items": executions})
    atomic_json(
        directory / "summary.json",
        {**envelope, "run_name": manifest["run_name"], **summarize(executions)},
    )
