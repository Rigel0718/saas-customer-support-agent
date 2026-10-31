import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.tools import TOOL_DEFINITIONS
from app.diagnosis.tools import execute_tool
from app.models import AuditEvent, Base
from app.seed import apply_seed, uid
from app.services import customer, policy
from tests.test_support_models import NOW

ORG = uid(1)
OTHER_ORG = uid(2)
ADMIN = uid(0x102)
MEMBER = uid(0x103)
OTHER_OWNER = uid(0x201)


def event(number, *, org=ORG, hours=0, actor=ADMIN, kind="user_login", target=None):
    return AuditEvent(
        id=uid(0xA000 + number),
        organization_id=org,
        occurred_at=NOW - timedelta(hours=hours),
        actor_user_id=actor,
        event_type=kind,
        target_user_id=target,
        payload={},
    )


@pytest.fixture
def events(session):
    rows = [
        event(1, hours=1, kind="role_changed", target=MEMBER),
        event(2, hours=2),
        # 동일 시각의 동점은 id 오름차순으로 정렬한다.
        event(4, hours=3),
        event(3, hours=3),
        event(5, hours=48, actor=None, kind="subscription_status_changed"),
        event(6, org=OTHER_ORG, hours=1, actor=OTHER_OWNER, kind="role_changed"),
    ]
    session.add_all(rows)
    session.flush()
    return rows


def ids(page):
    return [item.id for item in page.items]


def context(session, user=ADMIN):
    return customer.authenticate(session, user)


def test_orders_filters_and_truncates_within_organization(session, events):
    ctx = context(session)
    page = customer.get_audit_events(session, ctx, limit=50)
    assert ids(page) == [uid(0xA001), uid(0xA002), uid(0xA003), uid(0xA004), uid(0xA005)]
    assert page.total_count == 5 and page.truncated is False

    page = customer.get_audit_events(session, ctx, limit=2)
    assert ids(page) == [uid(0xA001), uid(0xA002)]
    assert page.total_count == 5 and page.truncated is True

    assert ids(
        customer.get_audit_events(session, ctx, target_user_id=MEMBER, limit=50)
    ) == [uid(0xA001)]
    assert ids(
        customer.get_audit_events(
            session, ctx, event_type="subscription_status_changed", limit=50
        )
    ) == [uid(0xA005)]
    assert ids(
        customer.get_audit_events(
            session,
            ctx,
            start_at=NOW - timedelta(hours=3),
            end_at=NOW - timedelta(hours=2),
            limit=50,
        )
    ) == [uid(0xA002), uid(0xA003), uid(0xA004)]
    assert customer.get_audit_events(session, ctx, actor_user_id=ADMIN, limit=50).total_count == 4


def test_other_organization_identifiers_return_nothing(session, events):
    ctx = context(session)
    for filters in ({"actor_user_id": OTHER_OWNER}, {"target_user_id": OTHER_OWNER}):
        page = customer.get_audit_events(session, ctx, limit=50, **filters)
        assert page.items == [] and page.total_count == 0 and page.truncated is False
    other = customer.get_audit_events(session, context(session, OTHER_OWNER), limit=50)
    assert ids(other) == [uid(0xA006)]


def test_read_does_not_change_events(session, events):
    before = [
        (e.id, e.occurred_at, e.event_type, e.payload)
        for e in session.scalars(select(AuditEvent).order_by(AuditEvent.id))
    ]
    customer.get_audit_events(session, context(session), limit=1)
    session.expire_all()
    after = [
        (e.id, e.occurred_at, e.event_type, e.payload)
        for e in session.scalars(select(AuditEvent).order_by(AuditEvent.id))
    ]
    assert before == after


def test_requires_caller_context(session):
    with pytest.raises(customer.CustomerError):
        customer.get_audit_events(session, None, limit=1)


@pytest.mark.parametrize(
    "row",
    [
        {"kind": "refund_issued"},
        {"org": uuid4()},
    ],
)
def test_constraints_reject_invalid_rows(session, row):
    session.add(event(9, **row))
    with pytest.raises(IntegrityError):
        session.flush()


def audit_arguments(**changes):
    return {
        "start_at": None,
        "end_at": None,
        "actor_user_id": None,
        "target_user_id": None,
        "event_type": None,
        "limit": 50,
    } | changes


def tool(session, args, user=ADMIN):
    return execute_tool(
        "get_audit_events",
        json.dumps(args),
        session,
        context(session, user),
        policy.load_policies(),
        3,
    )


def test_tool_definition_is_strict_and_has_no_organization():
    definition = next(d for d in TOOL_DEFINITIONS if d["name"] == "get_audit_events")
    parameters = definition["parameters"]
    assert definition["strict"] is True
    assert parameters["additionalProperties"] is False
    assert set(parameters["required"]) == set(parameters["properties"]) == set(
        audit_arguments()
    )
    assert "organization_id" not in parameters["properties"]


def test_tool_observation(session, events):
    result = tool(
        session,
        audit_arguments(
            target_user_id=str(MEMBER),
            end_at=NOW.isoformat(),
            limit=1,
        ),
    )
    observation = result.observation
    assert observation["ok"] is True and observation["trace_sequence"] == 3
    assert observation["data"]["total_count"] == 1
    assert observation["data"]["items"][0]["id"] == str(uid(0xA001))
    assert observation["data"]["items"][0]["event_type"] == "role_changed"


@pytest.mark.parametrize(
    "args",
    [
        audit_arguments(limit=0),
        audit_arguments(limit=51),
        audit_arguments(organization_id=str(OTHER_ORG)),
        audit_arguments(
            start_at=NOW.isoformat(), end_at=(NOW - timedelta(days=1)).isoformat()
        ),
        audit_arguments(start_at="2026-01-01T00:00:00"),
        audit_arguments(event_type="refund_issued"),
        {"limit": 10},
    ],
)
def test_tool_rejects_invalid_arguments(session, events, args):
    result = tool(session, args)
    assert result.arguments is None
    assert result.observation["error_code"] == "invalid_arguments"


def test_migration_adds_table_and_preserves_rows(engine):
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            with Session(connection, join_transaction_mode="create_savepoint") as session:
                apply_seed(session, reference_time=NOW)
                session.commit()
            connection.execute(text("DROP TABLE audit_events"))
            tables = [name for name in Base.metadata.tables if name != "audit_events"]
            before = {
                name: connection.execute(text(f"SELECT * FROM {name} ORDER BY id")).mappings().all()
                for name in tables
            }
            sql = Path("migrations/004_audit_events.sql").read_text()
            for statement in sql.split(";"):
                if statement.strip() not in ("", "BEGIN", "COMMIT"):
                    connection.execute(text(statement))
            for name in tables:
                rows = connection.execute(text(f"SELECT * FROM {name} ORDER BY id")).mappings().all()
                assert rows == before[name]
            migrated = {
                c["name"]: (str(c["type"]), c["nullable"])
                for c in inspect(connection).get_columns("audit_events")
            }
            constraints = {
                c["name"] for c in inspect(connection).get_check_constraints("audit_events")
            }
            indexes = {i["name"] for i in inspect(connection).get_indexes("audit_events")}
        finally:
            transaction.rollback()
    created = {
        c["name"]: (str(c["type"]), c["nullable"])
        for c in inspect(engine).get_columns("audit_events")
    }
    assert migrated == created
    assert constraints == {"audit_event_type"}
    assert indexes == {"ix_audit_events_org_occurred"}


def test_access_policy_keeps_existing_search_results():
    documents = policy.load_policies()

    def search(query):
        return [item["document_id"] for item in policy.search_policy(query, documents)["items"]]

    assert search("플랜") == ["plans"]
    assert search("권한") == ["permissions"]
    assert search("구독 결제") == ["troubleshooting", "permissions"]
    assert search("초대") == ["permissions", "plans", "troubleshooting"]
    assert search("접근") == ["workspace_access"]
