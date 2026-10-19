from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, text, update
from sqlalchemy.exc import SQLAlchemyError
from pydantic import ValidationError

from app.config import Settings
from app.agent.loop import add_trace, run_agent
from app.agent.tools import AgentFailure
from sqlalchemy.orm import Session

from app.models.support import AgentTrace, Diagnosis, SupportRequest
from app.schemas.support import (
    DiagnosisDTO,
    RequestCreate,
    RequestDTO,
    TraceDTO,
    TracesDTO,
)
from app.services.customer import CallerContext, CustomerError, require_context


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def get_request(
    session: Session, context: CallerContext, request_id: UUID
) -> SupportRequest:
    require_context(context)
    request = session.scalar(
        select(SupportRequest)
        .where(
            SupportRequest.id == request_id,
            SupportRequest.organization_id == context.organization_id,
        )
        .execution_options(populate_existing=True)
    )
    if request is None:
        raise CustomerError("not_found")
    return request


def request_dto(session: Session, request: SupportRequest) -> RequestDTO:
    diagnosis = None
    if request.status == "completed":
        diagnosis = session.scalar(
            select(Diagnosis).where(Diagnosis.support_request_id == request.id)
        )
    return RequestDTO(
        **{
            name: getattr(request, name)
            for name in RequestDTO.model_fields
            if name != "diagnosis"
        },
        diagnosis=DiagnosisDTO.model_validate(diagnosis) if diagnosis else None,
    )


def create_request(
    session: Session, context: CallerContext, body: RequestCreate
) -> RequestDTO:
    require_context(context)
    request = SupportRequest(
        id=uuid4(),
        requester_user_id=context.user_id,
        organization_id=context.organization_id,
        content=body.content,
        status="pending",
        created_at=utc_now(),
    )
    try:
        session.add(request)
        session.flush()
        result = request_dto(session, request)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


def get_traces(session: Session, context: CallerContext, request_id: UUID) -> TracesDTO:
    get_request(session, context, request_id)
    traces = session.scalars(
        select(AgentTrace)
        .where(AgentTrace.support_request_id == request_id)
        .order_by(AgentTrace.sequence)
    )
    return TracesDTO(items=[TraceDTO.model_validate(trace) for trace in traces])


def validate_diagnosis(raw: str, traces: list[TraceDTO]) -> DiagnosisDTO:
    try:
        diagnosis = DiagnosisDTO.model_validate_json(raw)
        observations = {
            trace.sequence: trace
            for trace in traces
            if trace.event_type == "tool_result" and trace.observation is not None
        }
        successful = False
        for evidence in diagnosis.evidence:
            trace = observations.get(evidence.trace_sequence)
            if trace is None:
                raise ValueError("Invalid observation reference")
            if trace.observation["ok"]:
                successful = True
            if evidence.policy_document_id is not None:
                if trace.tool_name != "search_policy" or not trace.observation["ok"]:
                    raise ValueError("Invalid policy reference")
                ids = {
                    item["document_id"] for item in trace.observation["data"]["items"]
                }
                if evidence.policy_document_id not in ids:
                    raise ValueError("Policy was not observed")
        if diagnosis.outcome == "diagnosed" and not successful:
            raise ValueError("Successful observation required")
        return diagnosis
    except (ValidationError, ValueError, TypeError, KeyError):
        raise AgentFailure("invalid_output") from None


def save_terminal(
    session: Session,
    context: CallerContext,
    request_id: UUID,
    diagnosis: DiagnosisDTO | None,
    error_code: str | None,
    traces: list[TraceDTO],
    now=utc_now,
) -> RequestDTO:
    # 종료 저장에는 Tool의 짧은 deadline 대신 별도 DB timeout을 적용한다.
    session.execute(
        text(
            "SELECT set_config('statement_timeout', '5000', true), set_config('lock_timeout', '5000', true)"
        )
    )
    request = get_request(session, context, request_id)
    if request.status != "running":
        if request.status in ("completed", "failed"):
            return request_dto(session, request)
        raise CustomerError("conflict")
    changed = session.execute(
        update(SupportRequest)
        .where(
            SupportRequest.id == request_id,
            SupportRequest.organization_id == context.organization_id,
            SupportRequest.status == "running",
        )
        .values(
            status="completed" if diagnosis else "failed",
            finished_at=now(),
            error_code=error_code,
        )
    )
    if changed.rowcount != 1:
        raise CustomerError("conflict")
    if diagnosis:
        session.add(
            Diagnosis(
                id=uuid4(),
                support_request_id=request_id,
                created_at=now(),
                **diagnosis.model_dump(mode="json"),
            )
        )
        session.flush()
    session.add_all(
        [
            AgentTrace(id=uuid4(), support_request_id=request_id, **trace.model_dump())
            for trace in traces
        ]
    )
    session.flush()
    result = request_dto(session, get_request(session, context, request_id))
    session.commit()
    return result


def diagnose_request(
    session: Session,
    context: CallerContext,
    request_id: UUID,
    settings: Settings,
    create: Callable | None = None,
    clock: Callable | None = None,
    now: Callable = utc_now,
) -> RequestDTO:
    require_context(context)
    try:
        claimed = session.execute(
            update(SupportRequest)
            .where(
                SupportRequest.id == request_id,
                SupportRequest.organization_id == context.organization_id,
                SupportRequest.status == "pending",
            )
            .values(status="running", started_at=now())
        )
        if claimed.rowcount == 0:
            request = get_request(session, context, request_id)
            if request.status == "completed":
                return request_dto(session, request)
            raise CustomerError("conflict")
        request = get_request(session, context, request_id)
        # commit으로 ORM 속성이 만료되기 전에 신뢰된 입력을 복사한다.
        from types import SimpleNamespace

        target = SimpleNamespace(
            requester_user_id=request.requester_user_id, content=request.content
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    traces: list[TraceDTO] = []
    diagnosis = None
    error_code = None
    try:
        try:
            raw = run_agent(
                session,
                context,
                target,
                settings,
                traces,
                create=create,
                now=now,
                **({"clock": clock} if clock else {}),
            )
            try:
                diagnosis = validate_diagnosis(raw, traces)
            except AgentFailure:
                add_trace(traces, "final_result", now, error_code="invalid_output")
                raise
            add_trace(traces, "final_result", now)
            add_trace(traces, "execution_completed", now)
        except AgentFailure as exc:
            error_code = exc.code
            if error_code == "invalid_output" and not any(
                trace.event_type == "final_result" for trace in traces
            ):
                add_trace(traces, "final_result", now, error_code=error_code)
            add_trace(traces, "execution_failed", now, error_code=error_code)
        except SQLAlchemyError:
            raise
        except Exception:
            error_code = "tool_error"
            add_trace(traces, "execution_failed", now, error_code=error_code)
        return save_terminal(
            session, context, request_id, diagnosis, error_code, traces, now
        )
    except SQLAlchemyError:
        session.rollback()
        '''
        commit 응답만 유실되었을 수 있으므로 저장된 종료 결과를 보호한다.
        즉, complete인데 네트워크등의 오류로 인해 commit에 실패한 경우를 대비하여 current.status == "running"인 경우에만 failed를 저장한다.
        이미 completed라면: 완료된 결과를 덮어쓰지 않는다.
        여전히 running이라면: database_error로 실패 저장을 시도한다.
        새 트랜잭션에서 running -> failed 저장을 한 번만 시도한다.
        '''
        try:
            current = get_request(session, context, request_id)
            if current.status == "running":
                failed_traces = list(traces)
                if (
                    failed_traces
                    and failed_traces[-1].event_type == "execution_completed"
                ):
                    failed_traces.pop()
                add_trace(
                    failed_traces, "execution_failed", now, error_code="database_error"
                )
                save_terminal(
                    session,
                    context,
                    request_id,
                    None,
                    "database_error",
                    failed_traces,
                    now,
                )
        except Exception:
            session.rollback()
        raise
