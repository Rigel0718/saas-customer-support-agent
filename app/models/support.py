from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SupportRequest(Base):
    __tablename__ = "support_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','completed','failed')",
            name="request_status",
        ),
        CheckConstraint(
            "char_length(content) BETWEEN 1 AND 4000", name="request_content"
        ),
        CheckConstraint(
            "(status = 'pending' AND execution_mode IS NULL) OR "
            "(status IN ('running','completed','failed') AND execution_mode IS NOT NULL "
            "AND execution_mode IN ('agent','workflow'))",
            name="request_execution_mode",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    requester_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    content: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    execution_mode: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String)


class Diagnosis(Base):
    __tablename__ = "diagnoses"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('diagnosed','needs_information','escalate')",
            name="diagnosis_outcome",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    support_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("support_requests.id", ondelete="RESTRICT"), unique=True
    )
    outcome: Mapped[str] = mapped_column(String)
    summary: Mapped[str] = mapped_column(String)
    causes: Mapped[list[str]] = mapped_column(JSONB)
    next_actions: Mapped[list[str]] = mapped_column(JSONB)
    evidence: Mapped[list[dict]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AgentTrace(Base):
    __tablename__ = "agent_traces"
    __table_args__ = (
        UniqueConstraint(
            "support_request_id", "sequence", name="trace_sequence_unique"
        ),
        CheckConstraint("sequence > 0", name="trace_sequence_positive"),
        CheckConstraint(
            "event_type IN ('execution_started','llm_response','tool_result','final_result','execution_failed','execution_completed')",
            name="trace_event",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    support_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("support_requests.id", ondelete="RESTRICT")
    )
    sequence: Mapped[int]
    event_type: Mapped[str] = mapped_column(String)
    step: Mapped[int | None]
    call_id: Mapped[str | None] = mapped_column(String)
    tool_name: Mapped[str | None] = mapped_column(String)
    arguments: Mapped[dict | None] = mapped_column(JSONB)
    observation: Mapped[dict | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String)
    duration_ms: Mapped[int | None]
    model: Mapped[str | None] = mapped_column(String)
    response_id: Mapped[str | None] = mapped_column(String)
    usage: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
