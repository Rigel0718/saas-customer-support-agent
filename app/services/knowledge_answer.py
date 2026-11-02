from collections.abc import Callable
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agent.knowledge import run_knowledge_agent
from app.config import Settings
from app.diagnosis.knowledge_tools import CITATION_SOURCES, observed_refs
from app.diagnosis.runtime import ExecutionFailure, add_trace
from app.schemas.knowledge_answer import KnowledgeAnswerDTO, KnowledgeAnswerResult
from app.schemas.support import ExecutionMode, FailureMetadata, TraceDTO
from app.services.knowledge import KnowledgeScope, require_scope
from app.workflow.knowledge import run_knowledge_workflow


def _failure(stage: str, reason: str, field_path: str | None = None):
    return ExecutionFailure(
        "invalid_output",
        failure_metadata=FailureMetadata(
            failure_stage=stage, failure_reason=reason, field_path=field_path
        ),
    )


def _field_path(location: tuple[str | int, ...]) -> str | None:
    """검증 위치를 알려진 출력 필드로만 투영한다."""
    if not location or location[0] not in ("outcome", "answer", "citations"):
        return None
    if len(location) == 1:
        return location[0]
    if location[0] != "citations" or type(location[1]) is not int:
        return None
    path = f"citations[{location[1]}]"
    if len(location) == 3 and location[2] in ("trace_sequence", "source", "ref"):
        return f"{path}.{location[2]}"
    return path if len(location) == 2 else None


def validate_answer(raw: str, traces: list[TraceDTO]) -> KnowledgeAnswerDTO:
    if not raw.strip():
        raise _failure("text_extraction", "empty_output")
    try:
        answer = KnowledgeAnswerDTO.model_validate_json(raw)
    except ValidationError as exc:
        first = exc.errors(
            include_input=False, include_context=False, include_url=False
        )[0]
        if first["type"] == "json_invalid":
            raise _failure("answer_json", "invalid_json") from None
        raise _failure(
            "answer_schema", "validation_error", _field_path(first["loc"])
        ) from None
    except (ValueError, TypeError):
        raise _failure("answer_schema", "validation_error") from None

    if answer.outcome == "answered" and not answer.citations:
        raise _failure("answer_schema", "validation_error", "citations")
    observations = {
        trace.sequence: trace
        for trace in traces
        if trace.event_type == "tool_result"
        and trace.tool_name in CITATION_SOURCES
        and trace.observation is not None
    }
    field_path = None
    try:
        for index, citation in enumerate(answer.citations):
            field_path = f"citations[{index}]"
            trace = observations.get(citation.trace_sequence)
            if (
                trace is None
                or not trace.observation["ok"]
                or CITATION_SOURCES[trace.tool_name] != citation.source
            ):
                raise _failure(
                    "citation_reference",
                    "invalid_citation_reference",
                    f"{field_path}.trace_sequence",
                )
            refs = observed_refs(trace.tool_name, trace.observation["data"])
            if citation.ref not in refs:
                raise _failure(
                    "citation_reference", "unobserved_citation", f"{field_path}.ref"
                )
    except (ValueError, TypeError, KeyError):
        raise _failure(
            "observation_validation", "invalid_observation", field_path
        ) from None
    return answer


def answer_question(
    session: Session,
    scope: KnowledgeScope,
    question: str,
    mode: ExecutionMode,
    settings: Settings,
    *,
    create: Callable | None = None,
    clock: Callable | None = None,
    now: Callable = lambda: datetime.now(timezone.utc),
) -> KnowledgeAnswerResult:
    require_scope(scope)
    if mode not in ("agent", "workflow"):
        raise ValueError("Invalid execution mode")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("Question required")

    traces: list[TraceDTO] = []
    raw = None
    try:
        runner = run_knowledge_agent if mode == "agent" else run_knowledge_workflow
        raw = runner(
            session,
            scope,
            question,
            settings,
            traces,
            create=create,
            now=now,
            **({"clock": clock} if clock else {}),
        )
        try:
            answer = validate_answer(raw, traces)
        except ExecutionFailure as exc:
            add_trace(
                traces,
                "final_result",
                now,
                error_code="invalid_output",
                failure_metadata=exc.failure_metadata,
            )
            raise
        add_trace(traces, "final_result", now)
        add_trace(traces, "execution_completed", now)
        return KnowledgeAnswerResult("completed", answer, None, None, raw, traces)
    except ExecutionFailure as exc:
        if exc.code == "invalid_output" and not any(
            trace.event_type == "final_result" for trace in traces
        ):
            add_trace(
                traces,
                "final_result",
                now,
                error_code=exc.code,
                failure_metadata=exc.failure_metadata,
            )
        add_trace(traces, "execution_failed", now, error_code=exc.code)
        return KnowledgeAnswerResult(
            "failed", None, exc.code, exc.failure_metadata, raw, traces
        )
    except SQLAlchemyError:
        session.rollback()
        add_trace(traces, "execution_failed", now, error_code="database_error")
        return KnowledgeAnswerResult(
            "failed", None, "database_error", None, raw, traces
        )
    except Exception:
        add_trace(traces, "execution_failed", now, error_code="tool_error")
        return KnowledgeAnswerResult("failed", None, "tool_error", None, raw, traces)
