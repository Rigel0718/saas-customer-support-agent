from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

AUDIT_EVENT_TYPES = (
    "role_changed",
    "subscription_status_changed",
    "member_invited",
    "user_login",
    "document_viewed",
    "document_edited",
)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ("
            + ",".join(f"'{event_type}'" for event_type in AUDIT_EVENT_TYPES)
            + ")",
            name="audit_event_type",
        ),
        Index("ix_audit_events_org_occurred", "organization_id", "occurred_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 삭제된 사용자의 이력도 보존하도록 사용자 FK를 두지 않는다. null actor는 system이다.
    actor_user_id: Mapped[UUID | None]
    event_type: Mapped[str] = mapped_column(String)
    target_user_id: Mapped[UUID | None]
    payload: Mapped[dict] = mapped_column(JSONB)
