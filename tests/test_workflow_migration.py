from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.schemas.support import RequestDTO
from tests.test_support_models import NOW, request


def test_smoke_preparation_creates_new_schema_and_can_repeat(engine):
    from sqlalchemy import inspect

    from app.models import Base
    from scripts.prepare_workflow_smoke_db import prepare_schema

    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            schema = "smoke_" + uuid4().hex
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            prepare_schema(conn)
            prepare_schema(conn)
            assert set(inspect(conn).get_table_names()) == set(Base.metadata.tables)
            columns = inspect(conn).get_columns("support_requests")
            assert "execution_mode" in {column["name"] for column in columns}
        finally:
            transaction.rollback()


@pytest.mark.parametrize(
    "status,mode",
    [
        ("pending", "agent"),
        ("pending", "workflow"),
        ("running", None),
        ("completed", None),
        ("failed", None),
        ("running", "bad"),
    ],
)
def test_mode_constraints_and_dto(session, status, mode):
    req = request(status=status, execution_mode=mode)
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(req)
        session.flush()
    with pytest.raises(ValidationError):
        RequestDTO(
            id=req.id,
            requester_user_id=req.requester_user_id,
            organization_id=req.organization_id,
            content=req.content,
            status=status,
            execution_mode=mode,
            created_at=NOW,
            started_at=None,
            finished_at=None,
            diagnosis=None,
            error_code=None,
        )


@pytest.mark.parametrize(
    "status,mode",
    [("pending", None)]
    + [
        (status, mode)
        for status in ("running", "completed", "failed")
        for mode in ("agent", "workflow")
    ],
)
def test_valid_mode_states(session, status, mode):
    req = request(status=status, execution_mode=mode)
    session.add(req)
    session.flush()
    assert session.get(type(req), req.id).execution_mode == mode


@pytest.mark.parametrize("automatic", [False, True])
def test_migration_preserves_legacy_data(engine, automatic):
    """별도 트랜잭션에서 실제 지원 테이블을 이전 스키마로 돌려 SQL을 적용한다."""
    from app.seed import apply_seed
    from sqlalchemy.orm import Session

    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            with Session(
                bind=conn, join_transaction_mode="create_savepoint"
            ) as session:
                apply_seed(session, reference_time=NOW)
                session.commit()
            conn.execute(
                text(
                    "ALTER TABLE support_requests DROP CONSTRAINT request_execution_mode"
                )
            )
            conn.execute(
                text("ALTER TABLE support_requests DROP COLUMN execution_mode")
            )
            ids = {}
            for status in ("pending", "running", "completed", "failed"):
                ids[status] = uuid4()
                conn.execute(
                    text("""INSERT INTO support_requests
                    (id,requester_user_id,organization_id,content,status,created_at)
                    SELECT :id,id,organization_id,'기존 문의',:status,:now
                    FROM users ORDER BY id LIMIT 1"""),
                    dict(id=ids[status], status=status, now=NOW),
                )
            conn.execute(
                text(
                    """INSERT INTO diagnoses
                (id,support_request_id,outcome,summary,causes,next_actions,evidence,created_at)
                VALUES (:id,:request,'escalate','기존 진단','[]','["지원 문의"]','[]',:now)"""
                ),
                dict(id=uuid4(), request=ids["completed"], now=NOW),
            )
            conn.execute(
                text("""INSERT INTO agent_traces
                (id,support_request_id,sequence,event_type,created_at)
                VALUES (:id,:request,1,'execution_completed',:now)"""),
                dict(id=uuid4(), request=ids["completed"], now=NOW),
            )
            tables = (
                "support_requests",
                "diagnoses",
                "agent_traces",
                "users",
                "organizations",
                "subscriptions",
                "payments",
            )
            before = {
                table: conn.execute(text(f"SELECT * FROM {table} ORDER BY id"))
                .mappings()
                .all()
                for table in tables
            }
            if automatic:
                from scripts.prepare_workflow_smoke_db import prepare_schema

                prepare_schema(conn)
                prepare_schema(conn)  # 이미 이행한 DB는 다시 변경하지 않는다.
            else:
                sql = Path("migrations/001_workflow_execution_mode.sql").read_text()
                # 이행 파일의 트랜잭션은 테스트의 바깥 트랜잭션으로 대체한다.
                for statement in sql.split(";"):
                    if statement.strip() not in ("", "BEGIN", "COMMIT"):
                        conn.execute(text(statement))
            for table in tables:
                after = (
                    conn.execute(text(f"SELECT * FROM {table} ORDER BY id"))
                    .mappings()
                    .all()
                )
                if table == "support_requests":
                    for row in after:
                        assert row["execution_mode"] == (
                            None if row["status"] == "pending" else "agent"
                        )
                    after = [
                        {k: v for k, v in row.items() if k != "execution_mode"}
                        for row in after
                    ]
                assert after == before[table]
            with pytest.raises(IntegrityError), conn.begin_nested():
                conn.execute(
                    text(
                        "UPDATE support_requests SET execution_mode=NULL WHERE status='running'"
                    )
                )
        finally:
            transaction.rollback()
