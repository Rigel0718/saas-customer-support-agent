import re
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
ExecutionMode = Literal["agent", "workflow"]
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
    # 정보가 부족해도 일부 확인된 원인은 유지할 수 있다.
    outcome: Literal["diagnosed", "needs_information", "escalate"]
    summary: Nonempty
    # 관찰 증거로 뒷받침되는 원인만 포함하며, 없으면 빈 배열이다.
    causes: list[Nonempty]
    # 추가 정보가 필요하면 검증할 사항이나 필요한 정보를 명시한다.
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
    execution_mode: ExecutionMode | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    diagnosis: DiagnosisDTO | None
    error_code: str | None

    @model_validator(mode="after")
    def execution_state(self):
        if (self.status == "pending") != (self.execution_mode is None):
            raise ValueError("Execution mode must match request status")
        return self


class LLMErrorMetadata(Schema):
    exception_type: Literal[
        "APITimeoutError",
        "APIConnectionError",
        "APIStatusError",
        "BadRequestError",
        "AuthenticationError",
        "PermissionDeniedError",
        "NotFoundError",
        "ConflictError",
        "UnprocessableEntityError",
        "RateLimitError",
        "InternalServerError",
        "TimeoutError",
        "ExecutionFailure",
        "UnknownError",
    ]
    is_timeout: Annotated[bool, Field(strict=True)]
    http_status: Annotated[int, Field(strict=True, ge=100, le=599)] | None


class LLMMetadata(Schema):
    request_timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    error: LLMErrorMetadata | None = None


FAILURE_REASONS = {
    "response_status": {"not_completed"},
    "agent_tool_call": {"invalid_call_id", "duplicate_call_id"},
    "workflow_output": {"unexpected_function_call"},
    "text_extraction": {"empty_output"},
    "diagnosis_json": {"invalid_json"},
    "diagnosis_schema": {"validation_error"},
    "evidence_reference": {"invalid_trace_reference", "no_successful_observation"},
    "policy_reference": {"invalid_policy_reference", "unobserved_policy_document"},
    "observation_validation": {"invalid_observation"},
}


def diagnosis_field_path(location: tuple[str | int, ...]) -> str | None:
    """Project a validation location onto known fields, never arbitrary JSON keys."""
    if not location or location[0] not in (
        "outcome",
        "summary",
        "causes",
        "next_actions",
        "evidence",
    ):
        return None
    root = location[0]
    if len(location) == 1:
        return root
    if (
        root not in ("causes", "next_actions", "evidence")
        or type(location[1]) is not int
        or location[1] < 0
    ):
        return None
    path = f"{root}[{location[1]}]"
    if len(location) == 2:
        return path
    if (
        root == "evidence"
        and len(location) == 3
        and location[2] in ("trace_sequence", "policy_document_id", "statement")
    ):
        return f"{path}.{location[2]}"
    return None


class FailureMetadata(Schema):
    failure_stage: Literal[
        "response_status",
        "agent_tool_call",
        "workflow_output",
        "text_extraction",
        "diagnosis_json",
        "diagnosis_schema",
        "evidence_reference",
        "policy_reference",
        "observation_validation",
    ]
    failure_reason: Literal[
        "not_completed",
        "invalid_call_id",
        "duplicate_call_id",
        "unexpected_function_call",
        "empty_output",
        "invalid_json",
        "validation_error",
        "invalid_trace_reference",
        "no_successful_observation",
        "invalid_policy_reference",
        "unobserved_policy_document",
        "invalid_observation",
    ]
    field_path: str | None = None

    @field_validator("field_path")
    @classmethod
    def known_field_path(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(
            r"(?:outcome|summary|(?:causes|next_actions)(?:\[[0-9]+\])?|"
            r"evidence(?:\[[0-9]+\](?:\.(?:trace_sequence|policy_document_id|statement))?)?)",
            value,
        ) is None:
            raise ValueError("Invalid diagnosis field path")
        return value

    @model_validator(mode="after")
    def valid_failure_pair(self):
        if self.failure_reason not in FAILURE_REASONS[self.failure_stage]:
            raise ValueError("Invalid failure stage/reason pair")
        return self


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
    llm_metadata: LLMMetadata | None = None
    failure_metadata: FailureMetadata | None = None
    created_at: datetime


class TracesDTO(Schema):
    items: list[TraceDTO]
