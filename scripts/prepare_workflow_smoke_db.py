"""Prepare the development schema without deleting existing smoke results."""

import os
from pathlib import Path

from sqlalchemy import Connection, inspect, text

from app.db import build_engine
from app.models import Base


def prepare_schema(connection: Connection) -> None:
    Base.metadata.create_all(connection)
    columns = inspect(connection).get_columns("support_requests")
    if any(column["name"] == "execution_mode" for column in columns):
        return

    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations/001_workflow_execution_mode.sql"
    )
    # The caller owns the transaction, including rollback on migration failure.
    for statement in migration.read_text().split(";"):
        if statement.strip() not in {"", "BEGIN", "COMMIT"}:
            connection.execute(text(statement))


def main() -> None:
    engine = build_engine(os.environ["DATABASE_URL"])
    try:
        with engine.begin() as connection:
            prepare_schema(connection)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
