from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.api.dependencies import get_session
from app.config import Settings
from app.db import session_scope, validate_test_database
from app.main import create_app
from app.models.customer import Organization, Payment, Subscription, User
from app.seed import apply_seed, uid
from app.services import customer
from tests.conftest import headers
from tests.test_support_models import NOW


def paths(org=1, user=0x101):
    return [
        f"/users/{uid(user)}",
        f"/organizations/{uid(org)}",
        f"/organizations/{uid(org)}/subscription",
        f"/organizations/{uid(org)}/payments",
    ]


def snapshot(session):
    return {
        model.__tablename__: [
            tuple(getattr(row, col.name) for col in model.__table__.columns)
            for row in session.scalars(select(model).order_by(model.id))
        ]
        for model in (Organization, User, Subscription, Payment)
    }


def test_seed_reproducible_and_states(session):
    before = snapshot(session)
    extra = Organization(id=uid(9000), name="Preserved", member_limit=0)
    session.add(extra)
    session.flush()
    apply_seed(session, reference_time=NOW)
    after = snapshot(session)
    assert after["organizations"][:-1] == before["organizations"]
    assert after["organizations"][-1][1] == "Preserved"
    for table in ("users", "subscriptions", "payments"):
        assert before[table] == after[table]
    apply_seed(session, reference_time=NOW)
    assert snapshot(session) == after
    assert {u.role for u in session.scalars(select(User))} == {
        "owner",
        "admin",
        "member",
    }
    assert {s.status for s in session.scalars(select(Subscription))} == {
        "active",
        "expired",
        "past_due",
    }
    assert {p.status for p in session.scalars(select(Payment))} == {
        "succeeded",
        "failed",
        "pending",
    }
    assert session.get(Subscription, uid(0x502)).expires_at == NOW - timedelta(days=50)
    assert session.get(Subscription, uid(0x501)).expires_at == NOW + timedelta(days=30)
    assert session.get(Payment, uid(0x601)).created_at.utcoffset() == timedelta(0)
    assert session.get(Payment, uid(0x601)).created_at == NOW


def test_seed_refreshes_dates_without_duplicates(session):
    later = NOW + timedelta(days=365)
    apply_seed(session, reference_time=later)
    session.expire_all()
    assert session.get(Subscription, uid(0x501)).expires_at == later + timedelta(days=30)
    assert session.get(Subscription, uid(0x502)).expires_at == later - timedelta(days=50)
    assert session.get(Payment, uid(0x601)).created_at == later
    assert session.get(Payment, uid(0x603)).paid_at == later - timedelta(days=30)
    assert {
        model: len(list(session.scalars(select(model))))
        for model in (Organization, User, Subscription, Payment)
    } == {Organization: 4, User: 6, Subscription: 3, Payment: 5}


@pytest.mark.parametrize(
    "record",
    [
        Organization(id=uid(900), name="Bad", member_limit=-1),
        User(id=uid(900), organization_id=uid(9999), role="owner"),
        User(id=uid(900), organization_id=uid(1), role="unknown"),
        Subscription(
            id=uid(900),
            organization_id=uid(1),
            plan="pro",
            status="active",
            expires_at=None,
        ),
        Subscription(
            id=uid(900),
            organization_id=uid(4),
            plan="bad",
            status="active",
            expires_at=None,
        ),
        Subscription(
            id=uid(900),
            organization_id=uid(4),
            plan="pro",
            status="bad",
            expires_at=None,
        ),
        Subscription(
            id=uid(900),
            organization_id=uid(9999),
            plan="pro",
            status="active",
            expires_at=None,
        ),
        Payment(
            id=uid(900),
            organization_id=uid(9999),
            status="pending",
            paid_at=None,
            created_at=NOW,
        ),
        Payment(
            id=uid(900),
            organization_id=uid(1),
            status="bad",
            paid_at=None,
            created_at=NOW,
        ),
        Payment(
            id=uid(900),
            organization_id=uid(1),
            status="succeeded",
            paid_at=None,
            created_at=NOW,
        ),
        Payment(
            id=uid(900),
            organization_id=uid(1),
            status="failed",
            paid_at=NOW,
            created_at=NOW,
        ),
        Payment(
            id=uid(900),
            organization_id=uid(1),
            status="pending",
            paid_at=NOW,
            created_at=NOW,
        ),
    ],
)
def test_constraints(session, record):
    before = snapshot(session)
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(record)
        session.flush()
    assert snapshot(session) == before


def test_restrict_organization_delete(session):
    with pytest.raises(IntegrityError), session.begin_nested():
        session.execute(delete(Organization).where(Organization.id == uid(1)))


@pytest.mark.parametrize("token", ["owner-token", "admin-token", "member-token"])
def test_api_and_services_success_read_only(client, session, token):
    before = snapshot(session)
    responses = [client.get(path, headers=headers(token)) for path in paths()]
    assert all(r.status_code == 200 for r in responses)
    user, org, sub, payment = [r.json() for r in responses]
    assert user == {
        "id": str(uid(0x101)),
        "organization_id": str(uid(1)),
        "role": "owner",
    }
    assert org == {
        "id": str(uid(1)),
        "name": "Healthy team",
        "member_count": 3,
        "member_limit": 10,
    }
    assert sub["status"] == "active" and sub["plan"] == "pro" and sub["expires_at"]
    assert [p["id"] for p in payment["items"]] == [
        str(uid(i)) for i in (0x601, 0x602, 0x603)
    ]
    assert payment["items"][0]["paid_at"] and payment["items"][1]["paid_at"] is None
    context = customer.authenticate(
        session,
        uid({"owner-token": 0x101, "admin-token": 0x102, "member-token": 0x103}[token]),
    )
    assert (
        customer.get_user(session, context, uid(0x101)).model_dump(mode="json") == user
    )
    assert (
        customer.get_organization(session, context, uid(1)).model_dump(mode="json")
        == org
    )
    assert customer.get_subscription(session, context, uid(1)).status == "active"
    assert [
        p.id for p in customer.get_payment_history(session, context, uid(1)).items
    ] == [uid(i) for i in (0x601, 0x602, 0x603)]
    assert snapshot(session) == before


@pytest.mark.parametrize(
    "org,token,status", [(2, "expired-token", "expired"), (3, "due-token", "past_due")]
)
def test_business_errors_return_data(client, org, token, status):
    response = client.get(paths(org)[2], headers=headers(token))
    assert response.status_code == 200 and response.json()["status"] == status
    organization = client.get(paths(org)[1], headers=headers(token)).json()
    if org == 2:
        assert organization["member_count"] == organization["member_limit"] == 1
    else:
        payments = client.get(paths(org)[3], headers=headers(token)).json()["items"]
        assert [p["status"] for p in payments] == ["failed", "succeeded"]


def test_missing_billing(client, session):
    assert client.get(paths(4)[2], headers=headers("empty-token")).status_code == 404
    assert client.get(paths(4)[3], headers=headers("empty-token")).json() == {
        "items": []
    }
    context = customer.authenticate(session, uid(0x401))
    assert customer.get_payment_history(session, context, uid(4)).items == []
    with pytest.raises(customer.CustomerError, match="not_found"):
        customer.get_subscription(session, context, uid(4))


@pytest.mark.parametrize("path", paths())
@pytest.mark.parametrize(
    "auth", [None, "Bearer invalid", "Bearer deleted-token", "Basic owner-token"]
)
def test_authentication_failures(client, path, auth):
    response = client.get(path, headers={"Authorization": auth} if auth else {})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "unauthorized"


@pytest.mark.parametrize("index", range(4))
def test_cross_org_missing_and_header_spoof(client, session, index):
    before = snapshot(session)
    foreign = client.get(paths(2, 0x201)[index], headers=headers())
    missing = client.get(paths(9999, 9999)[index], headers=headers())
    assert foreign.status_code == missing.status_code == 404
    assert (
        foreign.json()
        == missing.json()
        == {"detail": {"code": "not_found", "message": "Resource not found"}}
    )
    spoof = headers() | {"X-Organization-ID": str(uid(2))}
    assert client.get(paths()[index], headers=spoof).status_code == 200
    assert client.get(paths(2, 0x201)[index], headers=spoof).status_code == 404
    function = [
        customer.get_user,
        customer.get_organization,
        customer.get_subscription,
        customer.get_payment_history,
    ][index]
    context = customer.authenticate(session, uid(0x101))
    for target in [uid(0x201) if index == 0 else uid(2), uid(9999)]:
        with pytest.raises(customer.CustomerError, match="not_found"):
            function(session, context, target)
    with pytest.raises(customer.CustomerError, match="unauthorized"):
        function(session, None, uid(1))
    assert snapshot(session) == before


@pytest.mark.parametrize("path", paths())
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_write_methods_rejected(client, session, path, method):
    before = snapshot(session)
    assert client.request(method, path, headers=headers()).status_code == 405
    assert snapshot(session) == before


@pytest.mark.parametrize("path", paths())
def test_invalid_uuid(client, path):
    invalid = path.replace(str(uid(0x101)), "invalid").replace(str(uid(1)), "invalid")
    assert client.get(invalid, headers=headers()).status_code == 422


@pytest.mark.parametrize("path", paths())
def test_database_failure_sanitized(client, path):
    def broken_session():
        raise OperationalError("SELECT secret", {}, Exception("password secret"))
        yield

    client.app.dependency_overrides[get_session] = broken_session
    response = client.get(path, headers=headers())
    assert response.status_code == 503
    assert response.json() == {
        "detail": {"code": "service_unavailable", "message": "Database unavailable"}
    }


def test_config_no_auth_fallback(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://localhost/dev")
    for value in (
        "{}",
        "[]",
        "invalid",
        '{"":"00000000-0000-0000-0000-000000000101"}',
        '{"token":"bad"}',
    ):
        monkeypatch.setenv("DEV_BEARER_TOKENS", value)
        with pytest.raises(ValueError, match="DEV_BEARER_TOKENS"):
            create_app()
    monkeypatch.setenv(
        "DEV_BEARER_TOKENS", '{"token":"00000000-0000-0000-0000-000000000101"}'
    )
    assert Settings.from_env().bearer_tokens["token"] == uid(0x101)


def test_same_database_rejected():
    for test_url in (
        "postgresql+psycopg://other:password@127.0.0.1/dev",
        "sqlite:///test.db",
    ):
        with pytest.raises(ValueError):
            validate_test_database(test_url, "postgresql+psycopg://localhost/dev")


def test_session_cleanup_failure_and_success():
    from unittest.mock import patch

    for fail in (False, True):
        session = MagicMock()
        session.__enter__.return_value = session
        with patch("app.db.Session", return_value=session):
            if fail:
                with pytest.raises(RuntimeError), session_scope(None):
                    raise RuntimeError("failure")
                session.rollback.assert_called_once()
            else:
                with session_scope(None):
                    pass
                session.rollback.assert_not_called()
            session.__exit__.assert_called_once()


def test_session_rollback_postgresql(engine):
    with pytest.raises(RuntimeError):
        with session_scope(engine) as session:
            session.add(Organization(id=uid(8888), name="Rolled back", member_limit=1))
            session.flush()
            raise RuntimeError
    with session_scope(engine) as session:
        assert session.get(Organization, uid(8888)) is None
        assert session.scalar(text("SELECT 1")) == 1


def test_seed_transaction_rolls_back_on_failure(engine):
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        with pytest.raises(RuntimeError), session.begin():
            apply_seed(session, reference_time=NOW)
            raise RuntimeError("abort seed")
        for model in (Organization, User, Subscription, Payment):
            assert list(session.scalars(select(model))) == []
