from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit import AuditEvent
from app.models.customer import Organization, Payment, Subscription, User
from app.schemas.customer import (
    AuditEventDTO,
    AuditEventPageDTO,
    OrganizationDTO,
    PaymentDTO,
    PaymentHistoryDTO,
    SubscriptionDTO,
    UserDTO,
)


class CustomerError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CallerContext:
    user_id: UUID
    organization_id: UUID
    role: str


def require_context(context: CallerContext) -> None:
    if not isinstance(context, CallerContext):
        raise CustomerError("unauthorized")


def authenticate(session: Session, user_id: UUID) -> CallerContext:
    user = session.get(User, user_id)
    if user is None:
        raise CustomerError("unauthorized")
    return CallerContext(user.id, user.organization_id, user.role)


def get_user(session: Session, context: CallerContext, user_id: UUID) -> UserDTO:
    require_context(context)
    user = session.scalar(
        select(User).where(
            User.id == user_id, User.organization_id == context.organization_id
        )
    )
    if user is None:
        raise CustomerError("not_found")
    return UserDTO.model_validate(user)


def require_organization(
    session: Session, context: CallerContext, organization_id: UUID
) -> Organization:
    require_context(context)
    if organization_id != context.organization_id:
        raise CustomerError("not_found")
    organization = session.get(Organization, organization_id)
    if organization is None:
        raise CustomerError("not_found")
    return organization


def get_organization(
    session: Session, context: CallerContext, organization_id: UUID
) -> OrganizationDTO:
    org = require_organization(session, context, organization_id)
    count = session.scalar(
        select(func.count())
        .select_from(User)
        .where(User.organization_id == organization_id)
    )
    return OrganizationDTO(
        id=org.id, name=org.name, member_limit=org.member_limit, member_count=count
    )


def get_subscription(
    session: Session, context: CallerContext, organization_id: UUID
) -> SubscriptionDTO:
    require_organization(session, context, organization_id)
    subscription = session.scalar(
        select(Subscription).where(Subscription.organization_id == organization_id)
    )
    if subscription is None:
        raise CustomerError("not_found")
    return SubscriptionDTO.model_validate(subscription)


def get_payment_history(
    session: Session, context: CallerContext, organization_id: UUID
) -> PaymentHistoryDTO:
    require_organization(session, context, organization_id)
    payments = session.scalars(
        select(Payment)
        .where(Payment.organization_id == organization_id)
        .order_by(Payment.created_at.desc(), Payment.id.asc())
    )
    return PaymentHistoryDTO(
        items=[PaymentDTO.model_validate(payment) for payment in payments]
    )


def get_audit_events(
    session: Session,
    context: CallerContext,
    *,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    actor_user_id: UUID | None = None,
    target_user_id: UUID | None = None,
    event_type: str | None = None,
    limit: int,
) -> AuditEventPageDTO:
    require_context(context)
    # 조직 범위는 인증 맥락으로만 정하고 나머지 인자는 결과를 좁히는 필터다.
    conditions = [AuditEvent.organization_id == context.organization_id]
    if start_at is not None:
        conditions.append(AuditEvent.occurred_at >= start_at)
    if end_at is not None:
        conditions.append(AuditEvent.occurred_at <= end_at)
    if actor_user_id is not None:
        conditions.append(AuditEvent.actor_user_id == actor_user_id)
    if target_user_id is not None:
        conditions.append(AuditEvent.target_user_id == target_user_id)
    if event_type is not None:
        conditions.append(AuditEvent.event_type == event_type)
    total = session.scalar(
        select(func.count()).select_from(AuditEvent).where(*conditions)
    )
    events = session.scalars(
        select(AuditEvent)
        .where(*conditions)
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.asc())
        .limit(limit)
    )
    return AuditEventPageDTO(
        items=[AuditEventDTO.model_validate(event) for event in events],
        total_count=total,
        truncated=total > limit,
    )
