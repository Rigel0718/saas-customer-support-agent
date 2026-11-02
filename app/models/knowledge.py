from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

THREAD_KINDS = ("issue", "discussion")
# tsvector 크기 상한(1MB)을 넘지 않도록 검색 벡터는 앞부분만 쓴다.
# 저장 본문은 자르지 않는다.
SEARCH_TEXT_CHARS = 100_000


class KnowledgeDocument(Base):
    __tablename__ = "kb_documents"
    __table_args__ = (
        UniqueConstraint(
            "source", "snapshot_id", "version", "path", name="kb_document_path"
        ),
        Index("ix_kb_documents_search", "search_vector", postgresql_using="gin"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String)
    snapshot_id: Mapped[str] = mapped_column(String)
    # 날짜 없는 문서는 분기 버전(valid_from부터 유효)이고,
    # 날짜 있는 문서(changelog, blog)는 최신 버전에 published_on과 함께 있다.
    version: Mapped[str] = mapped_column(String)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_on: Mapped[date | None] = mapped_column(Date)
    path: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    url: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(String)
    search_vector = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', "
            f"left(title || ' ' || content, {SEARCH_TEXT_CHARS}))",
            persisted=True,
        ),
    )


class KnowledgeThread(Base):
    __tablename__ = "kb_threads"
    __table_args__ = (
        UniqueConstraint(
            "source", "snapshot_id", "kind", "number", name="kb_thread_number"
        ),
        CheckConstraint("kind IN ('issue','discussion')", name="kb_thread_kind"),
        CheckConstraint("state IN ('open','closed')", name="kb_thread_state"),
        Index("ix_kb_threads_search", "search_vector", postgresql_using="gin"),
        Index("ix_kb_threads_created", "source", "snapshot_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String)
    snapshot_id: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    number: Mapped[int]
    title: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(String)
    url: Mapped[str] = mapped_column(String)
    category: Mapped[str | None] = mapped_column(String)
    labels: Mapped[list[str]] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String)
    state_reason: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answer_post_id: Mapped[str | None] = mapped_column(String)
    answer_chosen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    truncated_posts: Mapped[bool]
    search_vector = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', "
            f"left(title || ' ' || body, {SEARCH_TEXT_CHARS}))",
            persisted=True,
        ),
    )


class KnowledgePost(Base):
    __tablename__ = "kb_posts"
    __table_args__ = (
        UniqueConstraint(
            "source", "snapshot_id", "thread_kind", "post_id", name="kb_post_id"
        ),
        ForeignKeyConstraint(
            ["source", "snapshot_id", "thread_kind", "thread_number"],
            [
                "kb_threads.source",
                "kb_threads.snapshot_id",
                "kb_threads.kind",
                "kb_threads.number",
            ],
            ondelete="CASCADE",
            name="kb_post_thread",
        ),
        Index(
            "ix_kb_posts_thread",
            "source",
            "snapshot_id",
            "thread_kind",
            "thread_number",
            "created_at",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String)
    snapshot_id: Mapped[str] = mapped_column(String)
    thread_kind: Mapped[str] = mapped_column(String)
    thread_number: Mapped[int]
    post_id: Mapped[str] = mapped_column(String)
    parent_post_id: Mapped[str | None] = mapped_column(String)
    body: Mapped[str] = mapped_column(String)
    # 작성자 로그인 대신 GitHub의 관계 값(OWNER, MEMBER, NONE 등)과 봇 여부만 저장한다.
    author_association: Mapped[str] = mapped_column(String)
    author_is_bot: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
