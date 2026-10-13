import os
from contextlib import contextmanager
from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


def build_engine(url: str) -> Engine:
    if make_url(url).get_backend_name() != "postgresql":
        raise ValueError("PostgreSQL is required")
    return create_engine(url, pool_pre_ping=True, connect_args={"options": "-c timezone=UTC"})


@contextmanager
def session_scope(engine: Engine) -> Generator[Session, None, None]:
    with Session(engine) as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise


def validate_test_database(test_url: str, development_url: str) -> None:
    test, dev = make_url(test_url), make_url(development_url)
    if test.get_backend_name() != "postgresql" or not test.database:
        raise ValueError("TEST_DATABASE_URL must use a dedicated PostgreSQL database")
    # Same database names are rejected even across host aliases to avoid accidental reuse.
    if test.database == dev.database:
        raise ValueError("Test and development database names must differ")


def main() -> None:
    from app.models.customer import Base

    engine = build_engine(os.environ["DATABASE_URL"])
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
