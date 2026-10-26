from datetime import timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Organization, User
from app.services import customer
from evaluation.dataset import EvaluationError, load_bundle
from evaluation.fixtures import (
    FixtureGuard,
    create_run_schema,
    customer_rows,
    customer_snapshot,
    insert_customers,
    record_id,
    validate_database_urls,
    verify_database,
)
from tests.test_support_models import NOW


@pytest.fixture(name="evaluation_db")
def evaluation_database(engine):
    run_id = uuid4()
    db, schema = create_run_schema(
        engine, engine.url.render_as_string(hide_password=False), run_id
    )
    try:
        yield db, run_id
    finally:
        db.dispose()
        # Test-owned schema only, never an evaluation runtime cleanup facility.
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


@pytest.mark.parametrize(
    "evaluation,dev,test",
    [
        ("", "postgresql://host/dev", "postgresql://host/test"),
        ("sqlite:///eval", "postgresql://host/dev", "postgresql://host/test"),
        ("postgresql://alias/dev", "postgresql://host/dev", "postgresql://host/test"),
        ("postgresql://alias/test", "postgresql://host/dev", "postgresql://host/test"),
        ("postgresql://host/eval", "", "postgresql://host/test"),
        ("postgresql://host/eval", "postgresql://host/dev", ""),
    ],
)
def test_database_rejected_without_connection(evaluation, dev, test):
    with pytest.raises(EvaluationError) as caught:
        validate_database_urls(evaluation, dev, test)
    assert str(caught.value) == "invalid_database_selection"
    assert "://" not in str(caught.value)


def test_actual_database_checked_before_write():
    fake = MagicMock()
    fake.connect.return_value.__enter__.return_value.scalar.return_value = "other"
    with pytest.raises(EvaluationError, match="database_mismatch"):
        verify_database(fake, "expected")
    fake.begin.assert_not_called()
    assert (
        validate_database_urls(
            "postgresql://host/eval", "postgresql://host/dev", "postgresql://host/test"
        )
        == "eval"
    )


def test_fixtures_commit_visible_neutral_names_and_cross_org_denied(evaluation_db):
    db, run_id = evaluation_db
    bundle = load_bundle()
    insert_customers(db, bundle, run_id, NOW)
    guard = FixtureGuard.capture(db, bundle, run_id, NOW, now=lambda: NOW)
    guard.check(now=lambda: NOW)
    with Session(db) as other:
        context = customer.authenticate(other, record_id(run_id, "C01", "requester"))
        org = customer.get_organization(other, context, context.organization_id)
        assert org.name == "Evaluation organization 1"
        assert (org.member_count, org.member_limit) == (2, 3)
        with pytest.raises(customer.CustomerError, match="not_found"):
            customer.get_organization(
                other, context, record_id(run_id, "C02", "organization")
            )
        assert len(list(other.scalars(select(User)))) == 10


def test_new_schema_preserves_sentinel_rejects_existing_and_isolates(
    engine, evaluation_db
):
    db, run_id = evaluation_db
    sentinel = Organization(id=uuid4(), name="sentinel", member_limit=99)
    with Session(db) as session:
        session.add(sentinel)
        session.commit()
        sentinel_id = sentinel.id
    bundle = load_bundle()
    insert_customers(db, bundle, run_id, NOW)
    with Session(db) as session:
        assert session.get(Organization, sentinel_id).name == "sentinel"
    with pytest.raises(EvaluationError, match="schema_preparation_failed"):
        create_run_schema(
            engine, engine.url.render_as_string(hide_password=False), run_id
        )
    assert len(customer_snapshot(db)["organizations"]) == 6
    assert all(
        row["name"] != "sentinel" for row in customer_snapshot(engine)["organizations"]
    )


def test_insert_rollback_on_duplicate(evaluation_db):
    db, run_id = evaluation_db
    # Conflict near end: earlier inserts in the same transaction must disappear.
    rows = customer_rows(load_bundle(), run_id, NOW)
    existing = next(
        row for row in rows if isinstance(row, Organization) and row.name.endswith("5")
    )
    with Session(db) as session:
        session.add(existing)
        session.commit()
    before = customer_snapshot(db)
    with pytest.raises(EvaluationError, match="fixture_insert_failed"):
        insert_customers(db, load_bundle(), run_id, NOW)
    assert customer_snapshot(db) == before


@pytest.mark.parametrize("change", ["user", "policy", "time"])
def test_guard_detects_mutations_before_model(evaluation_db, tmp_path, change):
    db, run_id = evaluation_db
    bundle = load_bundle()
    insert_customers(db, bundle, run_id, NOW)
    guard = FixtureGuard.capture(db, bundle, run_id, NOW, now=lambda: NOW)
    now = NOW
    if change == "user":
        with Session(db) as session:
            session.add(
                User(
                    id=uuid4(),
                    organization_id=record_id(run_id, "C01", "organization"),
                    role="member",
                )
            )
            session.commit()
    elif change == "policy":
        from app.services.policy import POLICY_DIR

        for path in POLICY_DIR.glob("*.json"):
            (tmp_path / path.name).write_bytes(path.read_bytes())
        with (tmp_path / "permissions.json").open("a") as file:
            file.write("\n")
        guard.policy_dir = tmp_path
    else:
        now += timedelta(days=40)
    calls = []
    with pytest.raises(EvaluationError):
        guard.check(now=lambda: now)
        calls.append(True)
    assert calls == []
