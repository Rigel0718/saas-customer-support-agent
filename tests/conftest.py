import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.routes import get_session
from app.config import Settings
from app.db import build_engine, validate_test_database
from app.main import create_app
from app.models.customer import Base
from app.seed import apply_seed, uid


@pytest.fixture(scope="session")
def engine():
    test_url = os.environ.get("TEST_DATABASE_URL")
    dev_url = os.environ.get("DATABASE_URL")
    if not test_url or not dev_url:
        pytest.fail("Set TEST_DATABASE_URL and DATABASE_URL to separate PostgreSQL databases")
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
        with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
            apply_seed(session)
            session.flush()
            yield session
        transaction.rollback()


@pytest.fixture
def client(session):
    settings = Settings(os.environ["TEST_DATABASE_URL"], {
        "owner-token": uid(0x101), "admin-token": uid(0x102), "member-token": uid(0x103),
        "expired-token": uid(0x201), "due-token": uid(0x301), "empty-token": uid(0x401),
        "deleted-token": uid(9999),
    })
    app = create_app(settings)
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield client


def headers(token="owner-token"):
    return {"Authorization": f"Bearer {token}"}
