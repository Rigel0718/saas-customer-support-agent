from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.diagnosis.runtime import ExecutionBudget, ExecutionFailure, add_trace
from app.schemas.support import TraceDTO
from app.services import customer, policy


class UserArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID


class OrganizationArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organization_id: UUID


class AuditEventArguments(BaseModel):
    # strict Tool schema를 위해 필터는 nullable 필수 인자로 둔다.
    model_config = ConfigDict(extra="forbid")
    start_at: AwareDatetime | None
    end_at: AwareDatetime | None
    actor_user_id: UUID | None
    target_user_id: UUID | None
    event_type: (
        Literal[
            "role_changed",
            "subscription_status_changed",
            "member_invited",
            "user_login",
            "document_viewed",
            "document_edited",
        ]
        | None
    )
    limit: Annotated[int, Field(ge=1, le=50)]

    @model_validator(mode="after")
    def ordered_range(self):
        if self.start_at and self.end_at and self.start_at > self.end_at:
            raise ValueError("start_at must not be after end_at")
        return self


ARGUMENTS = {
    "get_user": UserArguments,
    "get_organization": OrganizationArguments,
    "get_subscription": OrganizationArguments,
    "get_payment_history": OrganizationArguments,
    "search_policy": policy.PolicyQuery,
    "get_audit_events": AuditEventArguments,
}


@dataclass
class ToolResult:
    arguments: dict | None
    observation: dict


def execute_tool(
    name: str,
    raw_arguments: str,
    session: Session,
    context: customer.CallerContext,
    documents: list[policy.PolicyDocument],
    sequence: int,
) -> ToolResult:
    arguments = _validate_arguments(name, raw_arguments)
    return _execute_validated_tool(name, arguments, session, context, documents, sequence)


def _validate_arguments(
    name: str, raw_arguments: str, schema: type[BaseModel] | None = None
) -> BaseModel | None:
    if name not in ARGUMENTS:
        return None
    try:
        return (schema or ARGUMENTS[name]).model_validate_json(raw_arguments)
    except (ValidationError, TypeError, ValueError):
        return None


def _execute_validated_tool(
    name: str,
    arguments: BaseModel | None,
    session: Session,
    context: customer.CallerContext,
    documents: list[policy.PolicyDocument],
    sequence: int,
) -> ToolResult:
    def result(code=None, data=None, arguments=None):
        return ToolResult(
            arguments,
            {
                "ok": code is None,
                "data": data,
                "error_code": code,
                "trace_sequence": sequence,
            },
        )

    if name not in ARGUMENTS:
        return result("unknown_tool")
    if arguments is None:
        return result("invalid_arguments")
    validated = arguments.model_dump(mode="json")
    try:
        customer.require_context(context)
        if name == "search_policy":
            data = policy.search_policy(arguments.query, documents)
        else:
            data = getattr(customer, name)(
                session, context, **arguments.model_dump()
            ).model_dump(mode="json")
        return result(data=data, arguments=validated)
    except customer.CustomerError as exc:
        if exc.code == "not_found":
            return result("not_found", arguments=validated)
        raise ExecutionFailure("tool_error") from None
    except SQLAlchemyError:
        raise
    except Exception:
        raise ExecutionFailure("tool_error") from None


def execute_observed_tool(
    name: str,
    raw_arguments: str,
    session: Session,
    context: customer.CallerContext,
    documents: list[policy.PolicyDocument],
    traces: list[TraceDTO],
    budget: ExecutionBudget,
    *,
    step: int,
    call_id: str | None = None,
    now: Callable = lambda: datetime.now(timezone.utc),
    schema: type[BaseModel] | None = None,
) -> ToolResult:
    seconds = budget.remaining()
    arguments = _validate_arguments(name, raw_arguments, schema)
    validated = arguments.model_dump(mode="json") if arguments is not None else None
    before = budget.clock()
    try:
        timeout_ms = max(1, min(5000, int(seconds * 1000)))
        session.execute(
            text(
                "SELECT set_config('statement_timeout', :timeout, true), "
                "set_config('lock_timeout', :timeout, true)"
            ),
            {"timeout": str(timeout_ms)},
        )
        result = _execute_validated_tool(
            name, arguments, session, context, documents, len(traces) + 1
        )
    except Exception as exc:
        code = "database_error" if isinstance(exc, SQLAlchemyError) else "tool_error"
        add_trace(
            traces,
            "tool_result",
            now,
            step=step,
            call_id=call_id,
            tool_name=name,
            arguments=validated,
            error_code=code,
            duration_ms=max(0, int((budget.clock() - before) * 1000)),
        )
        if isinstance(exc, SQLAlchemyError):
            raise
        raise ExecutionFailure(code) from None
    add_trace(
        traces,
        "tool_result",
        now,
        step=step,
        call_id=call_id,
        tool_name=name,
        arguments=result.arguments,
        observation=result.observation,
        error_code=result.observation["error_code"],
        duration_ms=max(0, int((budget.clock() - before) * 1000)),
    )
    budget.remaining()
    return result
