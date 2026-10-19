from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.customer import Organization, Payment, Subscription, User
from app.schemas.customer import (
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
