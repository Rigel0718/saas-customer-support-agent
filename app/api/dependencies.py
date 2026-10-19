from typing import Annotated
from collections.abc import Iterator

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db import session_scope
from app.services import customer

bearer = HTTPBearer(auto_error=False)


def get_session(request: Request) -> Iterator[Session]:
    with session_scope(request.app.state.engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def get_context(
    request: Request,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> customer.CallerContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise customer.CustomerError("unauthorized")
    user_id = request.app.state.settings.bearer_tokens.get(credentials.credentials)
    if user_id is None:
        raise customer.CustomerError("unauthorized")
    return customer.authenticate(session, user_id)


ContextDep = Annotated[customer.CallerContext, Depends(get_context)]
