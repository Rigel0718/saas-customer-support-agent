"""Knowledge base model, as_of-scoped search and loader (test-owned DB schema)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from app.knowledge.loader import load_snapshot
from app.models import Base, KnowledgeDocument, KnowledgePost, KnowledgeThread
from app.seed import apply_seed
from app.services import knowledge
from app.services.knowledge import KnowledgeError, KnowledgeScope
from tests.test_support_models import NOW

AS_OF = datetime(2026, 6, 1, tzinfo=timezone.utc)
BEFORE = (AS_OF - timedelta(days=10)).isoformat()
AFTER = (AS_OF + timedelta(days=1)).isoformat()


def thread(kind, number, title, body="", created=BEFORE, **extra):
    return {
        "kind": kind,
        "number": number,
        "title": title,
        "body": body,
        "url": f"https://example.test/{kind}/{number}",
        "category": "Support" if kind == "discussion" else None,
        "labels": ["bug"],
        "state": "open",
        "state_reason": None,
        "created_at": created,
        "closed_at": None,
        "answer_post_id": None,
        "answer_chosen_at": None,
        "truncated_posts": False,
    } | extra


def post(kind, number, post_id, body, created=BEFORE, association="NONE", parent=None):
    return {
        "thread_kind": kind,
        "thread_number": number,
        "post_id": post_id,
        "parent_post_id": parent,
        "body": body,
        "author_association": association,
        "author_is_bot": False,
        "created_at": created,
    }


Q1 = (AS_OF - timedelta(days=60)).isoformat()
Q2 = (AS_OF - timedelta(days=5)).isoformat()
Q3 = (AS_OF + timedelta(days=20)).isoformat()


def document(path, content, version, valid_from, published=None, title=None):
    return {
        "version": version,
        "valid_from": valid_from,
        "published_on": published,
        "path": path,
        "title": title or path,
        "url": f"https://example.test/{path}",
        "content": content,
    }


def snapshot_data():
    return {
        "documents": [
            # 분기 버전 3개: Q1(과거), Q2(as_of 직전, 현재 버전), Q3(미래).
            document("docs/tracing", "Old tracing setup with exporter.", "q1", Q1),
            document(
                "docs/tracing",
                "Tracing exporter configuration with OTEL endpoint.",
                "q2",
                Q2,
                title="Tracing",
            ),
            document("docs/tracing", "Future tracing rewrite exporter.", "q3", Q3),
            document(
                "docs/self-host",
                "Self host with clickhouse and redis. " + "x " * 7000,
                "q2",
                Q2,
            ),
            document("docs/legacy", "Removed legacy page", "q1", Q1),
            document("docs/new-feature", "Brand new monitors feature", "q3", Q3),
            # 날짜 있는 문서는 최신 버전에만 있고 발표일로 거른다.
            document(
                "changelog/2026-05-20-monitors",
                "Monitors changelog",
                "latest",
                AFTER,
                published="2026-05-20",
            ),
            document(
                "changelog/2026-06-01-same-day",
                "Same day monitors changelog",
                "latest",
                AFTER,
                published="2026-06-01",
            ),
            document(
                "changelog/2026-07-01-future",
                "Future monitors changelog",
                "latest",
                AFTER,
                published="2026-07-01",
            ),
        ],
        "threads": [
            thread(
                "issue",
                1,
                "OTEL exporter timeout",
                "exporter times out",
                state="closed",
                state_reason="completed",
                closed_at=AFTER,
            ),
            thread(
                "discussion",
                2,
                "Answer chosen later",
                "exporter question",
                answer_post_id="a2",
                answer_chosen_at=AFTER,
            ),
            thread(
                "discussion",
                3,
                "Answer chosen before",
                "exporter question",
                answer_post_id="a3",
                answer_chosen_at=BEFORE,
                state="closed",
                closed_at=BEFORE,
            ),
            thread("issue", 4, "Future clickhouse migration", created=AFTER),
            thread("issue", 5, "Self question about tracing"),
            thread("issue", 6, "Many comments exporter"),
            # tsvector 상한(1MB)을 넘는 본문도 적재·검색되어야 한다.
            thread("issue", 7, "Huge log", " ".join(f"w{i}x" for i in range(200_000))),
            thread(
                "discussion",
                8,
                "Late answer",
                answer_post_id="d32",
                answer_chosen_at=BEFORE,
            ),
        ],
        "posts": [
            post("issue", 1, "p1", "earlier reply"),
            post("issue", 1, "p2", "later fix", created=AFTER, association="MEMBER"),
            post("discussion", 2, "a2", "answer two"),
            post(
                "discussion",
                3,
                "c3",
                "question detail",
                created=(AS_OF - timedelta(days=11)).isoformat(),
            ),
            post("discussion", 3, "a3", "answer three", association="MEMBER", parent="c3"),
        ]
        + [
            post("issue", 6, f"m{i:02d}", f"comment {i}", created=BEFORE)
            for i in range(31)
        ]
        + [post("discussion", 8, f"d{i:02d}", f"reply {i}") for i in range(35)],
    }


def manifest(snapshot="snap-1", status="complete"):
    return {"source": "demo", "snapshot_id": snapshot, "status": status}


@pytest.fixture
def kb(session):
    load_snapshot(session, manifest(), snapshot_data())
    session.flush()
    return KnowledgeScope("demo", "snap-1", AS_OF, exclude_thread=("issue", 5))


def counts(session):
    return [
        session.scalar(select(func.count()).select_from(model))
        for model in (KnowledgeDocument, KnowledgeThread, KnowledgePost)
    ]


def rows(session):
    return [
        sorted((r.id, r.source, r.snapshot_id) for r in session.scalars(select(model)))
        for model in (KnowledgeDocument, KnowledgeThread, KnowledgePost)
    ]


def test_reload_is_idempotent_and_snapshots_coexist(session, kb):
    first = rows(session)
    load_snapshot(session, manifest(), snapshot_data())
    session.flush()
    assert rows(session) == first
    load_snapshot(session, manifest("snap-2"), snapshot_data())
    session.flush()
    assert counts(session) == [2 * c for c in [9, 8, 71]]
    with pytest.raises(ValueError):
        load_snapshot(session, manifest("snap-3", status="incomplete"), snapshot_data())


def test_future_state_comments_and_answers_are_hidden(session, kb):
    issue = knowledge.get_thread(session, kb, "issue", 1)
    assert (
        issue.state == "open" and issue.closed_at is None and issue.state_reason is None
    )
    assert [p.post_id for p in issue.posts] == ["p1"]
    later = knowledge.get_thread(session, kb, "discussion", 2)
    assert later.has_answer is False and not any(p.is_answer for p in later.posts)
    chosen = knowledge.get_thread(session, kb, "discussion", 3)
    assert chosen.has_answer is True and chosen.state == "closed"
    assert [(p.post_id, p.parent_post_id, p.is_answer) for p in chosen.posts] == [
        ("c3", None, False),
        ("a3", "c3", True),
    ]
    assert "labels" not in chosen.model_dump()


def test_documents_follow_version_and_publication_rules(session, kb):
    tracing = knowledge.get_document(session, kb, "docs/tracing")
    assert tracing.content.startswith("Tracing exporter configuration")
    hits = knowledge.search_documents(session, kb, "monitors", limit=10)
    assert [h.path for h in hits.items] == ["changelog/2026-05-20-monitors"]
    for path in ("docs/legacy", "docs/new-feature", "changelog/2026-07-01-future"):
        with pytest.raises(KnowledgeError) as exc:
            knowledge.get_document(session, kb, path)
        assert exc.value.code == "not_found"
    found = knowledge.search_documents(session, kb, "tracing exporter", limit=10)
    assert [h.path for h in found.items] == ["docs/tracing"]
    early = KnowledgeScope("demo", "snap-1", AS_OF - timedelta(days=100))
    assert knowledge.search_documents(session, early, "tracing").items == []


def test_long_bodies_and_answers_beyond_shown_posts(session, kb):
    huge = knowledge.search_threads(session, kb, "Huge log")
    assert [h.number for h in huge.items] == [7]
    late = knowledge.get_thread(session, kb, "discussion", 8)
    assert late.has_answer is True and late.truncated_posts
    assert len(late.posts) == 30 and not any(p.is_answer for p in late.posts)


def test_future_excluded_and_missing_threads_are_indistinguishable(session, kb):
    for kind, number in (("issue", 4), ("issue", 5), ("issue", 999)):
        with pytest.raises(KnowledgeError) as exc:
            knowledge.get_thread(session, kb, kind, number)
        assert exc.value.code == "not_found"
    for query in ("Future clickhouse migration", "Self question about tracing"):
        found = knowledge.search_threads(session, kb, query, limit=10)
        assert {(h.kind, h.number) for h in found.items}.isdisjoint(
            {("issue", 4), ("issue", 5)}
        )


def test_search_contract(session, kb):
    before = counts(session)
    first = knowledge.search_threads(session, kb, "exporter", limit=10)
    again = knowledge.search_threads(session, kb, "exporter", limit=10)
    assert first == again and len(first.items) >= 3
    ranks = [(-h.rank, h.kind, h.number) for h in first.items]
    assert ranks == sorted(ranks)
    assert all(h.state == "open" for h in first.items if h.number == 1)
    only = knowledge.search_threads(session, kb, "exporter", kind="discussion")
    assert {h.kind for h in only.items} == {"discussion"}
    docs = knowledge.search_documents(session, kb, "clickhouse redis")
    assert [d.path for d in docs.items] == ["docs/self-host"]
    document = knowledge.get_document(session, kb, "docs/self-host")
    assert document.truncated and len(document.content) == knowledge.MAX_DOCUMENT_CHARS
    many = knowledge.get_thread(session, kb, "issue", 6)
    assert len(many.posts) == 30 and many.truncated_posts
    assert counts(session) == before


@pytest.mark.parametrize(
    "call",
    [
        lambda s, k: knowledge.search_threads(s, k, "x", limit=11),
        lambda s, k: knowledge.search_threads(s, k, "x", limit=0),
        lambda s, k: knowledge.search_threads(s, k, "  "),
        lambda s, k: knowledge.search_threads(s, k, "x" * 301),
        lambda s, k: knowledge.search_threads(s, k, "x", kind="pull"),
        lambda s, k: knowledge.get_thread(s, k, "issue", "1"),
        lambda s, k: knowledge.get_document(s, k, " "),
    ],
)
def test_invalid_arguments(session, kb, call):
    with pytest.raises(KnowledgeError) as exc:
        call(session, kb)
    assert exc.value.code == "invalid_arguments"


def test_scope_must_be_application_built(session, kb):
    for scope in (
        None,
        KnowledgeScope("demo", "snap-1", AS_OF.replace(tzinfo=None)),
        KnowledgeScope("", "snap-1", AS_OF),
    ):
        with pytest.raises(KnowledgeError) as exc:
            knowledge.search_documents(session, scope, "tracing")
        assert exc.value.code == "invalid_scope"
    other = KnowledgeScope("other", "snap-1", AS_OF)
    assert knowledge.search_documents(session, other, "tracing").items == []


def test_migration_matches_orm_and_preserves_rows(engine):
    tables = ("kb_posts", "kb_threads", "kb_documents")

    def structure(connection):
        found = {}
        for name in tables:
            found[name] = (
                {
                    c["name"]: (str(c["type"]), c["nullable"])
                    for c in inspect(connection).get_columns(name)
                },
                {i["name"] for i in inspect(connection).get_indexes(name)},
                {c["name"] for c in inspect(connection).get_unique_constraints(name)},
            )
        return found

    with engine.connect() as connection:
        created = structure(connection)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            with Session(
                connection, join_transaction_mode="create_savepoint"
            ) as session:
                apply_seed(session, reference_time=NOW)
                session.commit()
            for name in tables:
                connection.execute(text(f"DROP TABLE {name}"))
            others = [n for n in Base.metadata.tables if n not in tables]
            before = {
                n: connection.execute(text(f"SELECT * FROM {n} ORDER BY id"))
                .mappings()
                .all()
                for n in others
            }
            sql = Path("migrations/005_support_knowledge_base.sql").read_text()
            for statement in sql.split(";"):
                if statement.strip() not in ("", "BEGIN", "COMMIT"):
                    connection.execute(text(statement))
            for n in others:
                assert (
                    connection.execute(text(f"SELECT * FROM {n} ORDER BY id"))
                    .mappings()
                    .all()
                    == before[n]
                )
            migrated = structure(connection)
        finally:
            transaction.rollback()
    assert migrated == created
