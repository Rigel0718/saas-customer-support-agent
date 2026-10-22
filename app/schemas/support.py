from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Nonempty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
EventType = Literal[
    "execution_started",
    "llm_response",
    "tool_result",
    "final_result",
    "execution_failed",
    "execution_completed",
]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    @field_validator("*", mode="after")
    @classmethod
    def utc_dates(cls, value):
        if isinstance(value, datetime):
            if value.tzinfo is None:
                raise ValueError("Timezone required")
            return value.astimezone(timezone.utc)
        return value


class RequestCreate(Schema):
    content: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
    ]


class Evidence(Schema):
    trace_sequence: Annotated[int, Field(strict=True, gt=0)]
    policy_document_id: Nonempty | None
    statement: Nonempty


class DiagnosisDTO(Schema):
    outcome: Literal["diagnosed", "needs_information", "escalate"]
    summary: Nonempty
    causes: list[Nonempty]
    next_actions: list[Nonempty]
    evidence: list[Evidence]

    @model_validator(mode="after")
    def required_content(self):
        if self.outcome == "diagnosed" and (not self.causes or not self.evidence):
            raise ValueError("Diagnosis requires causes and evidence")
        if self.outcome != "diagnosed" and not self.next_actions:
            raise ValueError("Questions or escalation guidance required")
        return self


class RequestDTO(Schema):
    id: UUID
    requester_user_id: UUID
    organization_id: UUID
    content: str
    status: Literal["pending", "running", "completed", "failed"]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    diagnosis: DiagnosisDTO | None
    error_code: str | None


class TraceDTO(Schema):
    sequence: int
    event_type: EventType
    step: int | None = None
    call_id: str | None = None
    tool_name: str | None = None
    arguments: dict | None = None
    observation: dict | None = None
    error_code: str | None = None
    duration_ms: int | None = None
    model: str | None = None
    response_id: str | None = None
    usage: dict | None = None
    created_at: datetime


class TracesDTO(Schema):
    items: list[TraceDTO]
