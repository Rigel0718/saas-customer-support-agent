import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import httpx
import openai
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.models import AgentTrace, Base
from app.seed import apply_seed
from tests.conftest import headers
from tests.test_agent_loop import final
from tests.test_support_models import NOW


@pytest.mark.parametrize("mode", ["agent", "workflow"])
@pytest.mark.parametrize("failed", [False, True])
def test_llm_metadata_api_and_independent_session(
    durable_app, durable_db, mode, failed, caplog
):
    durable_app.state.settings = replace(
        durable_app.state.settings, llm_request_timeout_seconds=35
    )
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        if failed:
            raise openai.AuthenticationError(
                "sensitive-marker",
                response=httpx.Response(
                    401,
                    request=httpx.Request(
                        "POST",
                        "https://sensitive-marker.invalid",
                        headers={"Authorization": "sensitive-marker"},
                        content="sensitive-marker",
                    ),
                ),
                body={"error": "sensitive-marker"},
            )
        return final()

    durable_app.state.responses_create = fake
    with TestClient(durable_app) as client:
        created = client.post(
            "/support/requests", headers=headers(), json={"content": "초대 문의"}
        ).json()
        path = f"/support/requests/{created['id']}"
        result = client.post(
            path + "/diagnose", params={"mode": mode}, headers=headers()
        )
        assert result.status_code == 200
        assert result.json()["status"] == ("failed" if failed else "completed")
        assert result.json()["error_code"] == ("llm_error" if failed else None)
        traces = client.get(path + "/traces", headers=headers()).json()["items"]
        llm_traces = [t for t in traces if t["event_type"] == "llm_response"]
        assert len(llm_traces) == len(calls) == 1
        metadata = llm_traces[0]["llm_metadata"]
        assert metadata["request_timeout_seconds"] == calls[0]["timeout"] == 35
        assert metadata["error"] == (
            {
                "exception_type": "AuthenticationError",
                "is_timeout": False,
                "http_status": 401,
            }
            if failed
            else None
        )
        assert all(
            t["llm_metadata"] is None
            for t in traces
            if t["event_type"] != "llm_response"
        )
        with Session(durable_db) as other:
            stored = other.scalar(
                select(AgentTrace).where(
                    AgentTrace.support_request_id == created["id"],
                    AgentTrace.event_type == "llm_response",
                )
            )
            assert stored.llm_metadata == metadata
        assert "sensitive-marker" not in result.text + json.dumps(traces) + caplog.text
        assert (
            client.get(path + "/traces", headers=headers("expired-token")).status_code
            == 404
        )


def test_metadata_migration_preserves_legacy_rows_and_relations(engine):
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            with Session(
                bind=connection, join_transaction_mode="create_savepoint"
            ) as session:
                apply_seed(session, reference_time=NOW)
                session.commit()
            connection.execute(
                text("ALTER TABLE agent_traces DROP COLUMN llm_metadata")
            )
            request_id = uuid4()
            connection.execute(
                text("""
                INSERT INTO support_requests
                (id,requester_user_id,organization_id,content,status,execution_mode,created_at)
                SELECT :id,id,organization_id,'이전 문의','completed','agent',:now
                FROM users ORDER BY id LIMIT 1
            """),
                {"id": request_id, "now": NOW},
            )
            connection.execute(
                text("""
                INSERT INTO diagnoses
                (id,support_request_id,outcome,summary,causes,next_actions,evidence,created_at)
                VALUES (:id,:request,'escalate','이전 진단','[]','["지원 문의"]','[]',:now)
            """),
                {"id": uuid4(), "request": request_id, "now": NOW},
            )
            connection.execute(
                text("""
                INSERT INTO agent_traces
                (id,support_request_id,sequence,event_type,error_code,created_at)
                VALUES (:id,:request,1,'llm_response','llm_error',:now)
            """),
                {"id": uuid4(), "request": request_id, "now": NOW},
            )
            tables = list(Base.metadata.tables)
            before = {
                table: connection.execute(text(f"SELECT * FROM {table} ORDER BY id"))
                .mappings()
                .all()
                for table in tables
            }
            sql = Path("migrations/002_llm_trace_metadata.sql").read_text()
            for statement in sql.split(";"):
                if statement.strip() not in ("", "BEGIN", "COMMIT"):
                    connection.execute(text(statement))
            for table in tables:
                rows = (
                    connection.execute(text(f"SELECT * FROM {table} ORDER BY id"))
                    .mappings()
                    .all()
                )
                if table == "agent_traces":
                    assert all(row["llm_metadata"] is None for row in rows)
                    rows = [
                        {k: v for k, v in row.items() if k != "llm_metadata"}
                        for row in rows
                    ]
                assert rows == before[table]
            column = next(
                c
                for c in inspect(connection).get_columns("agent_traces")
                if c["name"] == "llm_metadata"
            )
            assert column["nullable"] and str(column["type"]) == "JSONB"
        finally:
            transaction.rollback()
    # The normal fixture starts from the new ORM, independently of the migration.
    column = next(
        c
        for c in inspect(engine).get_columns("agent_traces")
        if c["name"] == "llm_metadata"
    )
    assert column["nullable"] and str(column["type"]) == "JSONB"
