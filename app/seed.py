import os
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db import build_engine
from app.models.customer import Organization, Payment, Subscription, User

REFERENCE_TIME = datetime(2026, 1, 15, tzinfo=timezone.utc)


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
SUBSCRIPTIONS = [
    dict(id=uid(0x501), organization_id=uid(1), plan="pro", status="active", expires_at=datetime(2026, 2, 15, tzinfo=timezone.utc)),
    dict(id=uid(0x502), organization_id=uid(2), plan="free", status="expired", expires_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
    dict(id=uid(0x503), organization_id=uid(3), plan="enterprise", status="past_due", expires_at=None),
]
PAYMENTS = [
    dict(id=uid(0x601), organization_id=uid(1), status="succeeded", paid_at=REFERENCE_TIME, created_at=REFERENCE_TIME),
    dict(id=uid(0x602), organization_id=uid(1), status="pending", paid_at=None, created_at=REFERENCE_TIME),
    dict(id=uid(0x603), organization_id=uid(1), status="succeeded", paid_at=datetime(2025, 12, 15, tzinfo=timezone.utc), created_at=datetime(2025, 12, 15, tzinfo=timezone.utc)),
    dict(id=uid(0x604), organization_id=uid(3), status="failed", paid_at=None, created_at=REFERENCE_TIME),
    dict(id=uid(0x605), organization_id=uid(3), status="succeeded", paid_at=datetime(2025, 12, 15, tzinfo=timezone.utc), created_at=datetime(2025, 12, 15, tzinfo=timezone.utc)),
]
MANIFEST = [(Organization, ORGANIZATIONS), (User, USERS), (Subscription, SUBSCRIPTIONS), (Payment, PAYMENTS)]


def apply_seed(session: Session) -> None:
    """Caller owns the transaction; only manifest primary keys are upserted."""
    for model, rows in MANIFEST:
        statement = insert(model).values(rows)
        session.execute(statement.on_conflict_do_update(
            index_elements=[model.id],
            set_={column: getattr(statement.excluded, column) for column in rows[0] if column != "id"},
        ))


def main() -> None:
    engine = build_engine(os.environ["DATABASE_URL"])
    try:
        with Session(engine) as session, session.begin():
            apply_seed(session)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
