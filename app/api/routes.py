from uuid import UUID

from fastapi import APIRouter

from app.api.dependencies import ContextDep, SessionDep
from app.schemas.customer import (
    OrganizationDTO,
    PaymentHistoryDTO,
    SubscriptionDTO,
    UserDTO,
)
from app.services import customer

router = APIRouter()


@router.get("/users/{user_id}", response_model=UserDTO)
def user(user_id: UUID, session: SessionDep, context: ContextDep):
    return customer.get_user(session, context, user_id)


@router.get("/organizations/{organization_id}", response_model=OrganizationDTO)
def organization(organization_id: UUID, session: SessionDep, context: ContextDep):
    return customer.get_organization(session, context, organization_id)


@router.get(
    "/organizations/{organization_id}/subscription", response_model=SubscriptionDTO
)
def subscription(organization_id: UUID, session: SessionDep, context: ContextDep):
    return customer.get_subscription(session, context, organization_id)


@router.get(
    "/organizations/{organization_id}/payments", response_model=PaymentHistoryDTO
)
def payments(organization_id: UUID, session: SessionDep, context: ContextDep):
    return customer.get_payment_history(session, context, organization_id)
