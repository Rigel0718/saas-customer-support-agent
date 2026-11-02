"""Read-only support knowledge base search scoped to what was knowable at as_of."""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, false, func, not_, or_, select
from sqlalchemy.orm import Session

from app.models.knowledge import KnowledgeDocument, KnowledgePost, KnowledgeThread
from app.schemas.knowledge import (
    DocumentDTO,
    DocumentHit,
    DocumentSearchDTO,
    PostDTO,
    ThreadDTO,
    ThreadHit,
    ThreadSearchDTO,
)

MAX_LIMIT = 10
MAX_DOCUMENT_CHARS = 12_000
MAX_POSTS = 30
MAX_QUERY_CHARS = 300
SNIPPET_OPTIONS = "MaxWords=35, MinWords=15, MaxFragments=1"


class KnowledgeError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class KnowledgeScope:
    """Application이 정하는 검색 범위. LLM 인자로는 바꿀 수 없다."""

    source: str
    snapshot_id: str
    as_of: datetime
    exclude_thread: tuple[str, int] | None = None


def require_scope(scope: KnowledgeScope) -> None:
    if (
        not isinstance(scope, KnowledgeScope)
        or scope.as_of.tzinfo is None
        or not scope.source
        or not scope.snapshot_id
    ):
        raise KnowledgeError("invalid_scope")


def _query(query: str) -> str:
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
        raise KnowledgeError("invalid_arguments")
    return query.strip()


def _limit(limit: int) -> int:
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise KnowledgeError("invalid_arguments")
    return limit


def _visible_threads(scope: KnowledgeScope) -> list:
    conditions = [
        KnowledgeThread.source == scope.source,
        KnowledgeThread.snapshot_id == scope.snapshot_id,
        KnowledgeThread.created_at < scope.as_of,
    ]
    if scope.exclude_thread is not None:
        kind, number = scope.exclude_thread
        conditions.append(
            not_(and_(KnowledgeThread.kind == kind, KnowledgeThread.number == number))
        )
    return conditions


def _visible_documents(session: Session, scope: KnowledgeScope) -> list:
    """날짜 없는 문서는 as_of 이전 최신 버전만, 날짜 있는 문서는 그 전날까지 발표만."""
    version = session.scalar(
        select(func.max(KnowledgeDocument.valid_from)).where(
            KnowledgeDocument.source == scope.source,
            KnowledgeDocument.snapshot_id == scope.snapshot_id,
            KnowledgeDocument.published_on.is_(None),
            KnowledgeDocument.valid_from <= scope.as_of,
        )
    )
    undated = (
        and_(
            KnowledgeDocument.published_on.is_(None),
            KnowledgeDocument.valid_from == version,
        )
        if version is not None
        else false()
    )
    # 같은 날 발표는 시각을 알 수 없어 보수적으로 제외한다.
    dated = KnowledgeDocument.published_on < scope.as_of.astimezone(timezone.utc).date()
    return [
        KnowledgeDocument.source == scope.source,
        KnowledgeDocument.snapshot_id == scope.snapshot_id,
        or_(undated, dated),
    ]


def _state_as_of(thread: KnowledgeThread, as_of: datetime) -> str:
    closed = thread.closed_at is not None and thread.closed_at < as_of
    return "closed" if closed else "open"


def search_documents(
    session: Session, scope: KnowledgeScope, query: str, limit: int = 5
) -> DocumentSearchDTO:
    require_scope(scope)
    tsquery = func.websearch_to_tsquery("english", _query(query))
    rank = func.ts_rank_cd(KnowledgeDocument.search_vector, tsquery)
    rows = session.execute(
        select(
            KnowledgeDocument,
            rank.label("rank"),
            func.ts_headline(
                "english", KnowledgeDocument.content, tsquery, SNIPPET_OPTIONS
            ).label("snippet"),
        )
        .where(
            *_visible_documents(session, scope),
            KnowledgeDocument.search_vector.op("@@")(tsquery),
        )
        .order_by(rank.desc(), KnowledgeDocument.path.asc())
        .limit(_limit(limit))
    )
    return DocumentSearchDTO(
        items=[
            DocumentHit(
                path=document.path,
                title=document.title,
                url=document.url,
                rank=float(score),
                snippet=snippet,
            )
            for document, score, snippet in rows
        ]
    )


def get_document(session: Session, scope: KnowledgeScope, path: str) -> DocumentDTO:
    require_scope(scope)
    if not isinstance(path, str) or not path.strip():
        raise KnowledgeError("invalid_arguments")
    document = session.scalar(
        select(KnowledgeDocument).where(
            *_visible_documents(session, scope),
            KnowledgeDocument.path == path.strip(),
        )
    )
    if document is None:
        raise KnowledgeError("not_found")
    return DocumentDTO(
        path=document.path,
        title=document.title,
        url=document.url,
        content=document.content[:MAX_DOCUMENT_CHARS],
        truncated=len(document.content) > MAX_DOCUMENT_CHARS,
    )


def search_threads(
    session: Session,
    scope: KnowledgeScope,
    query: str,
    kind: str | None = None,
    limit: int = 5,
) -> ThreadSearchDTO:
    require_scope(scope)
    if kind not in (None, "issue", "discussion"):
        raise KnowledgeError("invalid_arguments")
    tsquery = func.websearch_to_tsquery("english", _query(query))
    rank = func.ts_rank_cd(KnowledgeThread.search_vector, tsquery)
    conditions = _visible_threads(scope) + [
        KnowledgeThread.search_vector.op("@@")(tsquery)
    ]
    if kind is not None:
        conditions.append(KnowledgeThread.kind == kind)
    rows = session.execute(
        select(
            KnowledgeThread,
            rank.label("rank"),
            func.ts_headline(
                "english", KnowledgeThread.body, tsquery, SNIPPET_OPTIONS
            ).label("snippet"),
        )
        .where(*conditions)
        .order_by(rank.desc(), KnowledgeThread.kind.asc(), KnowledgeThread.number.asc())
        .limit(_limit(limit))
    )
    return ThreadSearchDTO(
        items=[
            ThreadHit(
                kind=thread.kind,
                number=thread.number,
                title=thread.title,
                state=_state_as_of(thread, scope.as_of),
                created_at=thread.created_at,
                rank=float(score),
                snippet=snippet,
            )
            for thread, score, snippet in rows
        ]
    )


def get_thread(
    session: Session, scope: KnowledgeScope, kind: str, number: int
) -> ThreadDTO:
    require_scope(scope)
    if kind not in ("issue", "discussion") or type(number) is not int:
        raise KnowledgeError("invalid_arguments")
    thread = session.scalar(
        select(KnowledgeThread).where(
            *_visible_threads(scope),
            KnowledgeThread.kind == kind,
            KnowledgeThread.number == number,
        )
    )
    # 범위 밖(미래·제외) 스레드와 없는 스레드를 구분하지 않는다.
    if thread is None:
        raise KnowledgeError("not_found")
    posts = list(
        session.scalars(
            select(KnowledgePost)
            .where(
                KnowledgePost.source == scope.source,
                KnowledgePost.snapshot_id == scope.snapshot_id,
                KnowledgePost.thread_kind == kind,
                KnowledgePost.thread_number == number,
                KnowledgePost.created_at < scope.as_of,
            )
            .order_by(KnowledgePost.created_at.asc(), KnowledgePost.post_id.asc())
            .limit(MAX_POSTS + 1)
        )
    )
    # 채택 답변은 표시하는 댓글 30개 밖에 있을 수 있어 따로 확인한다.
    answer_visible = (
        thread.answer_post_id is not None
        and thread.answer_chosen_at is not None
        and thread.answer_chosen_at < scope.as_of
        and session.scalar(
            select(KnowledgePost.id).where(
                KnowledgePost.source == scope.source,
                KnowledgePost.snapshot_id == scope.snapshot_id,
                KnowledgePost.thread_kind == kind,
                KnowledgePost.thread_number == number,
                KnowledgePost.post_id == thread.answer_post_id,
                KnowledgePost.created_at < scope.as_of,
            )
        )
        is not None
    )
    state = _state_as_of(thread, scope.as_of)
    return ThreadDTO(
        kind=thread.kind,
        number=thread.number,
        title=thread.title,
        body=thread.body,
        url=thread.url,
        category=thread.category,
        state=state,
        state_reason=thread.state_reason if state == "closed" else None,
        created_at=thread.created_at,
        closed_at=thread.closed_at if state == "closed" else None,
        has_answer=answer_visible,
        posts=[
            PostDTO(
                post_id=post.post_id,
                parent_post_id=post.parent_post_id,
                body=post.body,
                author_association=post.author_association,
                created_at=post.created_at,
                is_answer=answer_visible and post.post_id == thread.answer_post_id,
            )
            for post in posts[:MAX_POSTS]
        ],
        truncated_posts=thread.truncated_posts or len(posts) > MAX_POSTS,
    )
