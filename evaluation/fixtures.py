"""Append-only run schemas and customer DTO validation. No seed or cleanup path."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from uuid import UUID, uuid5

from sqlalchemy import Engine, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.db import build_engine
from app.models import Base, Organization, Payment, Subscription, User
from app.services import customer
from app.services.policy import POLICY_DIR
from evaluation.dataset import Bundle, EvaluationError, object_hash, policy_snapshot


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def validate_database_urls(
    evaluation_url: str, development_url: str, test_url: str
) -> str:
    try:
        urls = [
            make_url(value) for value in (evaluation_url, development_url, test_url)
        ]
        if any(u.get_backend_name() != "postgresql" or not u.database for u in urls):
            raise ValueError
        if urls[0].database in (urls[1].database, urls[2].database):
            raise ValueError
        return urls[0].database
    except Exception:
        raise EvaluationError("invalid_database_selection") from None


def verify_database(engine: Engine, expected_name: str) -> None:
    try:
        with engine.connect() as connection:
            if connection.scalar(text("SELECT current_database()")) != expected_name:
                raise EvaluationError("database_mismatch")
    except EvaluationError:
        raise
    except Exception:
        raise EvaluationError("database_unavailable") from None


def create_run_schema(
    admin: Engine, database_url: str, run_id: UUID
) -> tuple[Engine, str]:
    schema = "eval_" + run_id.hex
    engine = None
    try:
        # CREATE SCHEMA rejects existing schemas; never reuse or fall back to public.
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = build_engine(database_url)

        @event.listens_for(engine, "connect")
        def search_path(connection, record):
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute(f'SET search_path TO "{schema}"')
            connection.autocommit = False

        Base.metadata.create_all(engine)
        return engine, schema
    except Exception:
        if engine is not None:
            engine.dispose()
        raise EvaluationError("schema_preparation_failed") from None


def record_id(run_id: UUID, case_id: str, key: str) -> UUID:
    return uuid5(run_id, f"{case_id}:{key}")


def customer_rows(bundle: Bundle, run_id: UUID, reference_time: datetime) -> list:
    if reference_time.tzinfo is None or reference_time.utcoffset() != timedelta(0):
        raise EvaluationError("utc_reference_required")
    rows = []
    for index, case in enumerate(bundle.dataset.cases, 1):
        identify = partial(record_id, run_id, case.case_id)
        organization_id = identify("organization")
        rows.append(
            Organization(
                id=organization_id,
                name=f"Evaluation organization {index}",
                member_limit=case.member_limit,
            )
        )
        rows.extend(
            User(id=identify(user.key), organization_id=organization_id, role=user.role)
            for user in case.users
        )
        rows.append(
            Subscription(
                id=identify("subscription"),
                organization_id=organization_id,
                plan=case.subscription.plan,
                status=case.subscription.status,
                expires_at=reference_time
                + timedelta(days=case.subscription.expires_offset_days),
            )
        )
        for index, payment in enumerate(case.payments):
            rows.append(
                Payment(
                    id=identify(f"payment-{index}"),
                    organization_id=organization_id,
                    status=payment.status,
                    created_at=reference_time
                    + timedelta(days=payment.created_offset_days),
                    paid_at=reference_time + timedelta(days=payment.paid_offset_days),
                )
            )
    return rows


def insert_customers(
    engine: Engine, bundle: Bundle, run_id: UUID, reference_time: datetime
) -> None:
    rows = customer_rows(bundle, run_id, reference_time)
    try:
        with Session(engine) as session, session.begin():
            session.add_all(row for row in rows if isinstance(row, Organization))
            session.flush()
            session.add_all(row for row in rows if not isinstance(row, Organization))
            session.flush()
    except Exception:
        raise EvaluationError("fixture_insert_failed") from None


def customer_snapshot(engine: Engine) -> dict:
    result = {}
    with Session(engine) as session:
        for model in (Organization, User, Subscription, Payment):
            rows = session.scalars(select(model).order_by(model.id)).all()
            result[model.__tablename__] = [
                {
                    col.name: (
                        value.isoformat()
                        if isinstance(value, datetime)
                        else str(value)
                        if isinstance(value, UUID)
                        else value
                    )
                    for col in model.__table__.columns
                    for value in [getattr(row, col.name)]
                }
                for row in rows
            ]
    return result


def validate_customer_dtos(
    engine: Engine,
    bundle: Bundle,
    run_id: UUID,
    reference_time: datetime,
    now: datetime,
) -> None:
    expected = customer_rows(bundle, run_id, reference_time)
    with Session(engine) as session:
        for index, case in enumerate(bundle.dataset.cases, 1):
            identify = partial(record_id, run_id, case.case_id)
            org_id = identify("organization")
            context = customer.authenticate(session, identify("requester"))
            if context.role != case.requester_role or context.organization_id != org_id:
                raise EvaluationError("fixture_context_mismatch")
            for user in case.users:
                dto = customer.get_user(session, context, identify(user.key))
                if dto.role != user.role or dto.organization_id != org_id:
                    raise EvaluationError("fixture_user_mismatch")
            org = customer.get_organization(session, context, org_id)
            if (org.member_count, org.member_limit, org.name) != (
                len(case.users),
                case.member_limit,
                f"Evaluation organization {index}",
            ):
                raise EvaluationError("fixture_organization_mismatch")
            sub = customer.get_subscription(session, context, org_id)
            wanted = next(
                row
                for row in expected
                if isinstance(row, Subscription) and row.organization_id == org_id
            )
            if (sub.id, sub.plan, sub.status, sub.expires_at) != (
                wanted.id,
                wanted.plan,
                wanted.status,
                wanted.expires_at,
            ):
                raise EvaluationError("fixture_subscription_mismatch")
            if (sub.expires_at > now) != (case.subscription.status == "active"):
                raise EvaluationError("fixture_time_conditions_changed")
            payments = customer.get_payment_history(session, context, org_id).items
            wanted_payment = next(
                row
                for row in expected
                if isinstance(row, Payment) and row.organization_id == org_id
            )
            if len(payments) != 1 or payments[0].model_dump() != {
                col.name: getattr(wanted_payment, col.name)
                for col in Payment.__table__.columns
            }:
                raise EvaluationError("fixture_payment_mismatch")


@dataclass
class FixtureGuard:
    engine: Engine
    bundle: Bundle
    run_id: UUID
    reference_time: datetime
    snapshot: dict
    policy_dir: Path = POLICY_DIR

    @classmethod
    def capture(
        cls,
        engine,
        bundle,
        run_id,
        reference_time,
        *,
        now=utc_now,
        policy_dir=POLICY_DIR,
    ):
        guard = cls(
            engine,
            bundle,
            run_id,
            reference_time,
            customer_snapshot(engine),
            policy_dir,
        )
        guard.check(now=now)
        return guard

    def check(self, *, now=utc_now) -> None:
        try:
            if object_hash(customer_snapshot(self.engine)) != object_hash(
                self.snapshot
            ):
                raise EvaluationError("fixture_changed")
            if policy_snapshot(self.policy_dir) != self.bundle.provenance["policies"]:
                raise EvaluationError("policy_changed")
            validate_customer_dtos(
                self.engine, self.bundle, self.run_id, self.reference_time, now()
            )
        except EvaluationError:
            raise
        except Exception:
            raise EvaluationError("fixture_validation_failed") from None
