"""Idempotent load of one snapshot into the knowledge base tables."""

from datetime import date, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.knowledge import KnowledgeDocument, KnowledgePost, KnowledgeThread


def record_id(*parts) -> UUID:
    return uuid5(NAMESPACE_URL, "kb:" + ":".join(str(part) for part in parts))


def _time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _upsert(session: Session, model, rows: list[dict], constraint: str) -> None:
    if not rows:
        return
    statement = insert(model).values(rows)
    columns = {key: statement.excluded[key] for key in rows[0] if key not in ("id",)}
    session.execute(
        statement.on_conflict_do_update(constraint=constraint, set_=columns)
    )


def load_snapshot(
    session: Session, manifest: dict, data: dict[str, list[dict]]
) -> dict:
    if manifest.get("status") != "complete":
        raise ValueError("only complete snapshots can be loaded")
    source, snapshot = manifest["source"], manifest["snapshot_id"]
    scope = {"source": source, "snapshot_id": snapshot}
    documents = [
        {
            **scope,
            **doc,
            "id": record_id(source, snapshot, "doc", doc["version"], doc["path"]),
            "valid_from": _time(doc["valid_from"]),
            "published_on": (
                date.fromisoformat(doc["published_on"]) if doc["published_on"] else None
            ),
        }
        for doc in data["documents"]
    ]
    threads = [
        {
            **scope,
            **thread,
            "id": record_id(source, snapshot, thread["kind"], thread["number"]),
            "created_at": _time(thread["created_at"]),
            "closed_at": _time(thread["closed_at"]),
            "answer_chosen_at": _time(thread["answer_chosen_at"]),
        }
        for thread in data["threads"]
    ]
    posts = [
        {
            **scope,
            **post,
            "id": record_id(source, snapshot, post["thread_kind"], post["post_id"]),
            "created_at": _time(post["created_at"]),
        }
        for post in data["posts"]
    ]
    # PostgreSQL 매개변수 수 한도를 넘지 않도록 나눠서 넣는다.
    # 스레드를 먼저 넣어 FK를 지킨다.
    for model, rows, constraint in (
        (KnowledgeDocument, documents, "kb_document_path"),
        (KnowledgeThread, threads, "kb_thread_number"),
        (KnowledgePost, posts, "kb_post_id"),
    ):
        for start in range(0, len(rows), 500):
            _upsert(session, model, rows[start : start + 500], constraint)
    return {"documents": len(documents), "threads": len(threads), "posts": len(posts)}
