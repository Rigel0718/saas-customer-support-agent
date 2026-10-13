from typing import Annotated
from collections.abc import Iterator
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db import session_scope
from app.schemas import OrganizationDTO, PaymentHistoryDTO, SubscriptionDTO, UserDTO
from app.services import customer

router = APIRouter()
bearer = HTTPBearer(auto_error=False)


def get_session(request: Request) -> Iterator[Session]:
    with session_scope(request.app.state.engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def get_context(request: Request, session: SessionDep,
                credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> customer.CallerContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise customer.CustomerError("unauthorized")
    user_id = request.app.state.settings.bearer_tokens.get(credentials.credentials)
    if user_id is None:
        raise customer.CustomerError("unauthorized")
    return customer.authenticate(session, user_id)


ContextDep = Annotated[customer.CallerContext, Depends(get_context)]


@router.get("/users/{user_id}", response_model=UserDTO)
def user(user_id: UUID, session: SessionDep, context: ContextDep):
    return customer.get_user(session, context, user_id)


@router.get("/organizations/{organization_id}", response_model=OrganizationDTO)
def organization(organization_id: UUID, session: SessionDep, context: ContextDep):
    return customer.get_organization(session, context, organization_id)


@router.get("/organizations/{organization_id}/subscription", response_model=SubscriptionDTO)
def subscription(organization_id: UUID, session: SessionDep, context: ContextDep):
    return customer.get_subscription(session, context, organization_id)


@router.get("/organizations/{organization_id}/payments", response_model=PaymentHistoryDTO)
def payments(organization_id: UUID, session: SessionDep, context: ContextDep):
    return customer.get_payment_history(session, context, organization_id)
