import os
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db import build_engine
from app.models.customer import Organization, Payment, Subscription, User


def uid(number: int) -> UUID:
    return UUID(int=number)


ORGANIZATIONS = [
    dict(id=uid(1), name="Healthy team", member_limit=10),
    dict(id=uid(2), name="Expired team", member_limit=1),
    dict(id=uid(3), name="Past due team", member_limit=5),
    dict(id=uid(4), name="No billing team", member_limit=5),
]
USERS = [
    dict(id=uid(0x101), organization_id=uid(1), role="owner"),
    dict(id=uid(0x102), organization_id=uid(1), role="admin"),
    dict(id=uid(0x103), organization_id=uid(1), role="member"),
    dict(id=uid(0x201), organization_id=uid(2), role="owner"),
    dict(id=uid(0x301), organization_id=uid(3), role="owner"),
    dict(id=uid(0x401), organization_id=uid(4), role="owner"),
]


def build_manifest(reference_time: datetime) -> list[tuple[type, list[dict]]]:
    """Build customer scenarios relative to one UTC reference time."""
    subscriptions = [
        dict(
            id=uid(0x501),
            organization_id=uid(1),
            plan="pro",
            status="active",
            expires_at=reference_time + timedelta(days=30),
        ),
        dict(
            id=uid(0x502),
            organization_id=uid(2),
            plan="free",
            status="expired",
            expires_at=reference_time - timedelta(days=50),
        ),
        dict(
            id=uid(0x503),
            organization_id=uid(3),
            plan="enterprise",
            status="past_due",
            expires_at=None,
        ),
    ]
    payments = [
        dict(
            id=uid(0x601),
            organization_id=uid(1),
            status="succeeded",
            paid_at=reference_time,
            created_at=reference_time,
        ),
        dict(
            id=uid(0x602),
            organization_id=uid(1),
            status="pending",
            paid_at=None,
            created_at=reference_time,
        ),
        dict(
            id=uid(0x603),
            organization_id=uid(1),
            status="succeeded",
            paid_at=reference_time - timedelta(days=30),
            created_at=reference_time - timedelta(days=30),
        ),
        dict(
            id=uid(0x604),
            organization_id=uid(3),
            status="failed",
            paid_at=None,
            created_at=reference_time,
        ),
        dict(
            id=uid(0x605),
            organization_id=uid(3),
            status="succeeded",
            paid_at=reference_time - timedelta(days=30),
            created_at=reference_time - timedelta(days=30),
        ),
    ]
    return [
        (Organization, ORGANIZATIONS),
        (User, USERS),
        (Subscription, subscriptions),
        (Payment, payments),
    ]


def apply_seed(session: Session, *, reference_time: datetime | None = None) -> None:
    """Upsert seed rows relative to now; caller owns the transaction."""
    if reference_time is None:
        reference_time = datetime.now(timezone.utc)
    for model, rows in build_manifest(reference_time):
        statement = insert(model).values(rows)
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[model.id],
                set_={
                    column: getattr(statement.excluded, column)
                    for column in rows[0]
                    if column != "id"
                },
            )
        )


def main() -> None:
    engine = build_engine(os.environ["DATABASE_URL"])
    try:
        with Session(engine) as session, session.begin():
            apply_seed(session)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
