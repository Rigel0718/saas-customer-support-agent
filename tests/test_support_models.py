from datetime import datetime, timezone, timedelta
from uuid import uuid4
import subprocess
import sys

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.models import Base, SupportRequest, Diagnosis, AgentTrace
from app.models.customer import Base as OldBase
from app.schemas.support import RequestCreate, DiagnosisDTO, TraceDTO
from app.seed import uid

NOW = datetime(2026, 1, 15, tzinfo=timezone.utc)


def request(**overrides):
    values = dict(
        id=uuid4(),
        requester_user_id=uid(0x101),
        organization_id=uid(1),
        content="문의",
        status="pending",
        created_at=NOW,
    )
    return SupportRequest(**(values | overrides))


def test_registration_without_router():
    assert Base is OldBase
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.models import Base; import sys; assert len(Base.metadata.tables)==7; assert 'app.api.routes' not in sys.modules",
        ],
        check=True,
    )


@pytest.mark.parametrize("content", ["", "  ", "a" * 4001, None, 1])
def test_content_invalid(content):
    with pytest.raises(ValidationError):
        RequestCreate(content=content)


def test_schema_contract():
    assert RequestCreate(content=" 문의 ").content == "문의"
    with pytest.raises(ValidationError):
        RequestCreate(content="문의", role="owner")
    trace = TraceDTO(
        sequence=1,
        event_type="execution_started",
        created_at=NOW.astimezone(timezone(timedelta(hours=9))),
    )
    data = trace.model_dump(mode="json")
    assert data["created_at"] == "2026-01-15T00:00:00Z"
    assert data["usage"] is None and data["observation"] is None


@pytest.mark.parametrize("outcome", ["diagnosed", "needs_information", "escalate"])
def test_result_requirements(outcome):
    with pytest.raises(ValidationError):
        DiagnosisDTO(
            outcome=outcome, summary="요약", causes=[], next_actions=[], evidence=[]
        )


@pytest.mark.parametrize(
    "overrides",
    [
        dict(status="bad"),
        dict(content=""),
        dict(requester_user_id=uuid4()),
        dict(organization_id=uuid4()),
    ],
)
def test_request_constraints(session, overrides):
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(request(**overrides))
        session.flush()


@pytest.mark.parametrize(
    "kind",
    [
        "outcome",
        "duplicate_diagnosis",
        "diagnosis_fk",
        "trace_fk",
        "sequence",
        "duplicate_sequence",
        "event",
    ],
)
def test_result_trace_constraints(session, kind):
    req = request()
    session.add(req)
    session.flush()

    def diagnosis(**kw):
        return Diagnosis(
            **(
                dict(
                    id=uuid4(),
                    support_request_id=req.id,
                    outcome="escalate",
                    summary="지원 문의",
                    causes=[],
                    next_actions=["담당자 문의"],
                    evidence=[],
                    created_at=NOW,
                )
                | kw
            )
        )

    def trace(**kw):
        return AgentTrace(
            **(
                dict(
                    id=uuid4(),
                    support_request_id=req.id,
                    sequence=1,
                    event_type="execution_started",
                    created_at=NOW,
                )
                | kw
            )
        )

    session.add(diagnosis())
    session.add(trace())
    session.flush()
    extra_request = request()
    session.add(extra_request)
    session.flush()
    bad = {
        "outcome": lambda: diagnosis(
            outcome="bad", support_request_id=extra_request.id
        ),
        "duplicate_diagnosis": diagnosis,
        "diagnosis_fk": lambda: diagnosis(support_request_id=uuid4()),
        "trace_fk": lambda: trace(support_request_id=uuid4()),
        "sequence": lambda: trace(sequence=0),
        "duplicate_sequence": trace,
        "event": lambda: trace(sequence=2, event_type="bad"),
    }[kind]()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(bad)
        session.flush()
