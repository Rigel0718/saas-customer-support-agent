"""PostgreSQL/API coverage in test-owned schemas, with Fake Responses only."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models import AgentTrace, Base, Diagnosis
from app.seed import apply_seed
from app.services import support
from tests.conftest import headers
from tests.test_agent_loop import ScriptedResponses, evidence, final, response
from tests.test_support_models import NOW


@pytest.mark.parametrize("mode", ["agent", "workflow"])
@pytest.mark.parametrize(
    "output,stage",
    [
        (final(), None),
        (response([], status="incomplete"), "response_status"),
        (response([]), "text_extraction"),
        (final(summary=""), "diagnosis_schema"),
        (final("diagnosed", [evidence(999)]), "evidence_reference"),
    ],
)
def test_failure_metadata_api_and_independent_session(
    durable_app, durable_db, mode, output, stage, caplog
):
    fake = ScriptedResponses([output, final()])
    durable_app.state.responses_create = fake
    with TestClient(durable_app) as client:
        created = client.post(
            "/support/requests", headers=headers(), json={"content": "문의"}
        ).json()
        path = f"/support/requests/{created['id']}"
        result = client.post(
            path + "/diagnose", params={"mode": mode}, headers=headers()
        )
        assert result.status_code == 200
        data = result.json()
        assert data["status"] == ("failed" if stage else "completed")
        assert data["error_code"] == ("invalid_output" if stage else None)
        assert client.get(path, headers=headers()).json() == data
        traces = client.get(path + "/traces", headers=headers()).json()["items"]
        finals = [t for t in traces if t["event_type"] == "final_result"]
        assert len(finals) == 1
        metadata = finals[0]["failure_metadata"]
        if stage:
            assert metadata["failure_stage"] == stage
            assert data["diagnosis"] is None
            assert traces[-1]["event_type"] == "execution_failed"
            assert client.post(
                path + "/diagnose", params={"mode": mode}, headers=headers()
            ).status_code == 409
        else:
            assert metadata is None
            assert data["diagnosis"] is not None
        assert all(
            t["failure_metadata"] is None for t in traces
            if t["event_type"] != "final_result"
        )
        assert len(fake.calls) == 1
        assert "failure_metadata" not in json.dumps(fake.calls)
        with Session(durable_db) as other:
            stored = other.scalar(select(AgentTrace).where(
                AgentTrace.support_request_id == created["id"],
                AgentTrace.event_type == "final_result",
            ))
            assert stored.failure_metadata == metadata
            if stage:
                assert other.scalar(select(Diagnosis).where(
                    Diagnosis.support_request_id == created["id"]
                )) is None
        assert client.get(path + "/traces", headers=headers("expired-token")).status_code == 404
        assert client.get(path + "/traces").status_code == 401
        assert "sensitive-marker" not in result.text + json.dumps(traces) + caplog.text


def test_failure_metadata_migration_preserves_legacy_rows(engine):
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            with Session(connection, join_transaction_mode="create_savepoint") as session:
                apply_seed(session, reference_time=NOW)
                session.commit()
            connection.execute(text("ALTER TABLE agent_traces DROP COLUMN failure_metadata"))
            request_id = uuid4()
            connection.execute(text("""
                INSERT INTO support_requests
                (id,requester_user_id,organization_id,content,status,execution_mode,created_at)
                SELECT :id,id,organization_id,'이전 문의','completed','agent',:now
                FROM users ORDER BY id LIMIT 1
            """), {"id": request_id, "now": NOW})
            connection.execute(text("""
                INSERT INTO diagnoses
                (id,support_request_id,outcome,summary,causes,next_actions,evidence,created_at)
                VALUES (:id,:request,'escalate','이전 진단','[]','["지원 문의"]','[]',:now)
            """), {"id": uuid4(), "request": request_id, "now": NOW})
            connection.execute(text("""
                INSERT INTO agent_traces
                (id,support_request_id,sequence,event_type,llm_metadata,created_at)
                VALUES (:id,:request,1,'llm_response',CAST(:metadata AS JSONB),:now)
            """), {
                "id": uuid4(), "request": request_id, "now": NOW,
                "metadata": json.dumps({"request_timeout_seconds": 7, "error": None}),
            })
            tables = list(Base.metadata.tables)
            before = {
                name: connection.execute(text(f"SELECT * FROM {name} ORDER BY id")).mappings().all()
                for name in tables
            }
            sql = Path("migrations/003_diagnosis_failure_metadata.sql").read_text()
            for statement in sql.split(";"):
                if statement.strip() not in ("", "BEGIN", "COMMIT"):
                    connection.execute(text(statement))
            for name in tables:
                rows = connection.execute(text(f"SELECT * FROM {name} ORDER BY id")).mappings().all()
                if name == "agent_traces":
                    assert all(row["failure_metadata"] is None for row in rows)
                    rows = [{k: v for k, v in row.items() if k != "failure_metadata"} for row in rows]
                assert rows == before[name]
            column = next(c for c in inspect(connection).get_columns("agent_traces") if c["name"] == "failure_metadata")
            assert column["nullable"] and str(column["type"]) == "JSONB"
        finally:
            transaction.rollback()
    column = next(c for c in inspect(engine).get_columns("agent_traces") if c["name"] == "failure_metadata")
    assert column["nullable"] and str(column["type"]) == "JSONB"


@pytest.mark.parametrize("recover", [False, True])
def test_terminal_failure_rolls_back_metadata(durable_app, durable_db, monkeypatch, recover):
    durable_app.state.responses_create = ScriptedResponses([response([])])
    original = support.save_terminal
    attempts = []

    def save(session, context, request_id, diagnosis, code, traces, now):
        attempts.append(code)
        if len(attempts) == 1 or not recover:
            original_commit = session.commit

            def fail_commit():
                raise OperationalError("sensitive-marker", {}, Exception("sensitive-marker"))

            monkeypatch.setattr(session, "commit", fail_commit)
            try:
                return original(session, context, request_id, diagnosis, code, traces, now)
            finally:
                monkeypatch.setattr(session, "commit", original_commit)
        return original(session, context, request_id, diagnosis, code, traces, now)

    monkeypatch.setattr(support, "save_terminal", save)
    with TestClient(durable_app) as client:
        created = client.post(
            "/support/requests", headers=headers(), json={"content": "문의"}
        ).json()
        path = f"/support/requests/{created['id']}"
        result = client.post(path + "/diagnose", headers=headers())
        assert result.status_code == 503
        assert "sensitive-marker" not in result.text
        stored = client.get(path, headers=headers()).json()
        traces = client.get(path + "/traces", headers=headers()).json()["items"]
        assert stored["diagnosis"] is None
        if recover:
            assert stored["status"] == "failed" and stored["error_code"] == "database_error"
            assert sum(t["failure_metadata"] is not None for t in traces) == 1
        else:
            assert stored["status"] == "running"
            assert traces == []
