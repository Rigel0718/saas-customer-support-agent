from uuid import UUID

from fastapi import APIRouter, Request

from app.api.dependencies import ContextDep, SessionDep
from app.schemas.support import RequestCreate, RequestDTO, TracesDTO
from app.services import support

router = APIRouter(prefix="/support/requests")


@router.post("", response_model=RequestDTO, status_code=201)
def create(body: RequestCreate, session: SessionDep, context: ContextDep):
    return support.create_request(session, context, body)


@router.get("/{request_id}", response_model=RequestDTO)
def get(request_id: UUID, session: SessionDep, context: ContextDep):
    return support.request_dto(
        session, support.get_request(session, context, request_id)
    )


@router.get("/{request_id}/traces", response_model=TracesDTO)
def traces(request_id: UUID, session: SessionDep, context: ContextDep):
    return support.get_traces(session, context, request_id)


@router.post("/{request_id}/diagnose", response_model=RequestDTO)
def diagnose(
    request_id: UUID, session: SessionDep, context: ContextDep, request: Request
):
    return support.diagnose_request(
        session,
        context,
        request_id,
        request.app.state.settings,
        create=getattr(request.app.state, "responses_create", None),
        now=getattr(request.app.state, "utc_now", support.utc_now),
    )
