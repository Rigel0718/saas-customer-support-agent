import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.dependencies import get_session
from app.config import Settings
from app.db import build_engine, validate_test_database
from app.main import create_app
from app.models import Base
from app.seed import apply_seed, uid
from tests.test_support_models import NOW


@pytest.fixture(scope="session")
def engine():
    test_url = os.environ.get("TEST_DATABASE_URL")
    dev_url = os.environ.get("DATABASE_URL")
    if not test_url or not dev_url:
        pytest.fail(
            "Set TEST_DATABASE_URL and DATABASE_URL to separate PostgreSQL databases"
        )
    validate_test_database(test_url, dev_url)
    admin = build_engine(test_url)
    schema = "test_" + uuid4().hex
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    db = build_engine(test_url)
    from sqlalchemy import event

    @event.listens_for(db, "connect")
    def set_schema(connection, record):
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO "{schema}"')
        connection.autocommit = False

    Base.metadata.create_all(db)
    try:
        yield db
    finally:
        db.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def session(engine):
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(
            bind=connection, join_transaction_mode="create_savepoint"
        ) as session:
            apply_seed(session, reference_time=NOW)
            session.flush()
            yield session
        transaction.rollback()


@pytest.fixture
def client(session):
    settings = Settings(
        os.environ["TEST_DATABASE_URL"],
        {
            "owner-token": uid(0x101),
            "admin-token": uid(0x102),
            "member-token": uid(0x103),
            "expired-token": uid(0x201),
            "due-token": uid(0x301),
            "empty-token": uid(0x401),
            "deleted-token": uid(9999),
        },
    )
    app = create_app(settings)
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield client


def headers(token="owner-token"):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def durable_db(engine):
    """Savepoint를 쓰지 않아 다른 연결에서 실제 commit을 확인한다."""
    from sqlalchemy import delete
    from app.models import (
        AgentTrace,
        Diagnosis,
        SupportRequest,
        Payment,
        Subscription,
        User,
        Organization,
    )

    with Session(engine) as seed_session:
        apply_seed(seed_session, reference_time=NOW)
        seed_session.commit()
    try:
        yield engine
    finally:
        with Session(engine) as cleanup:
            for model in (
                AgentTrace,
                Diagnosis,
                SupportRequest,
                Payment,
                Subscription,
                User,
                Organization,
            ):
                cleanup.execute(delete(model))
            cleanup.commit()


@pytest.fixture
def durable_app(durable_db):
    settings = Settings(
        os.environ["TEST_DATABASE_URL"],
        {
            "owner-token": uid(0x101),
            "admin-token": uid(0x102),
            "member-token": uid(0x103),
            "expired-token": uid(0x201),
            "due-token": uid(0x301),
            "empty-token": uid(0x401),
        },
    )
    app = create_app(settings)

    def independent_session():
        with Session(durable_db) as session:
            yield session

    app.dependency_overrides[get_session] = independent_session
    app.state.utc_now = lambda: NOW
    return app
