from collections.abc import Callable
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.diagnosis.runtime import ExecutionBudget, ExecutionFailure, add_trace
from app.diagnosis.tools import ToolResult
from app.schemas.support import TraceDTO
from app.services import knowledge
from app.services.knowledge import KnowledgeError, KnowledgeScope

QUERY_RULES = (
    "검색어는 영어 키워드 2~4개로 쓴다. 모든 단어를 포함한 결과만 나온다(AND). "
    "대안 표현은 or로 잇는다. 질문 문장을 통째로 넣지 않는다."
)


class SearchDocumentsArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    limit: Annotated[int, Field(ge=1, le=knowledge.MAX_LIMIT)]


class DocumentArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str


class SearchThreadsArguments(BaseModel):
    # strict Tool schema를 위해 kind 필터는 nullable 필수 인자로 둔다.
    model_config = ConfigDict(extra="forbid")
    query: str
    kind: Literal["issue", "discussion"] | None
    limit: Annotated[int, Field(ge=1, le=knowledge.MAX_LIMIT)]


class ThreadArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["issue", "discussion"]
    number: Annotated[int, Field(ge=1)]


ARGUMENTS = {
    "search_documents": SearchDocumentsArguments,
    "get_document": DocumentArguments,
    "search_threads": SearchThreadsArguments,
    "get_thread": ThreadArguments,
}

# 인용 검증에서 Tool 관찰을 근거 종류로 대응시킨다.
CITATION_SOURCES = {
    "search_documents": "document",
    "get_document": "document",
    "search_threads": "thread",
    "get_thread": "thread",
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": ARGUMENTS[name].model_json_schema(),
        "strict": True,
    }
    for name, description in {
        "search_documents": f"제품 문서 검색, 관련도순 path·제목·발췌. {QUERY_RULES}",
        "get_document": "문서 path로 본문 조회, 최대 12,000자",
        "search_threads": (
            f"과거 지원 스레드(Issue·Discussion) 검색, 관련도순 ref·제목·상태·발췌. "
            f"{QUERY_RULES}"
        ),
        "get_thread": "스레드 본문과 댓글 조회, 채택 답변 표시",
    }.items()
]


def thread_ref(kind: str, number: int) -> str:
    return f"{kind}-{number}"


def observed_refs(name: str, data: dict) -> set[str]:
    if name == "search_documents":
        return {item["path"] for item in data["items"]}
    if name == "get_document":
        return {data["path"]}
    if name == "search_threads":
        return {item["ref"] for item in data["items"]}
    if name == "get_thread":
        return {data["ref"]}
    raise ValueError("Unknown knowledge tool")


def _validate_arguments(name: str, raw_arguments: str) -> BaseModel | None:
    if name not in ARGUMENTS:
        return None
    try:
        return ARGUMENTS[name].model_validate_json(raw_arguments)
    except (ValidationError, TypeError, ValueError):
        return None


def _call_service(
    name: str, arguments: BaseModel, session: Session, scope: KnowledgeScope
) -> dict:
    if name == "search_documents":
        result = knowledge.search_documents(
            session, scope, arguments.query, limit=arguments.limit
        )
        return {
            "items": [
                {"path": hit.path, "title": hit.title, "snippet": hit.snippet}
                for hit in result.items
            ]
        }
    if name == "get_document":
        document = knowledge.get_document(session, scope, arguments.path)
        return {
            "path": document.path,
            "title": document.title,
            "content": document.content,
            "truncated": document.truncated,
        }
    if name == "search_threads":
        result = knowledge.search_threads(
            session, scope, arguments.query, kind=arguments.kind, limit=arguments.limit
        )
        return {
            "items": [
                {
                    "ref": thread_ref(hit.kind, hit.number),
                    "title": hit.title,
                    "state": hit.state,
                    "created_at": hit.created_at.isoformat(),
                    "snippet": hit.snippet,
                }
                for hit in result.items
            ]
        }
    thread = knowledge.get_thread(session, scope, arguments.kind, arguments.number)
    return {
        "ref": thread_ref(thread.kind, thread.number),
        "title": thread.title,
        "body": thread.body,
        "state": thread.state,
        "has_answer": thread.has_answer,
        "posts": [post.model_dump(mode="json") for post in thread.posts],
        "truncated_posts": thread.truncated_posts,
    }


def _execute(
    name: str,
    arguments: BaseModel | None,
    session: Session,
    scope: KnowledgeScope,
    sequence: int,
) -> ToolResult:
    def result(code=None, data=None, arguments=None):
        return ToolResult(
            arguments,
            {
                "ok": code is None,
                "data": data,
                "error_code": code,
                "trace_sequence": sequence,
            },
        )

    if name not in ARGUMENTS:
        return result("unknown_tool")
    if arguments is None:
        return result("invalid_arguments")
    validated = arguments.model_dump(mode="json")
    try:
        knowledge.require_scope(scope)
        return result(
            data=_call_service(name, arguments, session, scope), arguments=validated
        )
    except KnowledgeError as exc:
        if exc.code in ("not_found", "invalid_arguments"):
            return result(exc.code, arguments=validated)
        raise ExecutionFailure("tool_error") from None
    except SQLAlchemyError:
        raise
    except Exception:
        raise ExecutionFailure("tool_error") from None


def execute_knowledge_tool(
    name: str,
    raw_arguments: str,
    session: Session,
    scope: KnowledgeScope,
    traces: list[TraceDTO],
    budget: ExecutionBudget,
    *,
    step: int,
    call_id: str | None = None,
    now: Callable = lambda: datetime.now(timezone.utc),
) -> ToolResult:
    seconds = budget.remaining()
    arguments = _validate_arguments(name, raw_arguments)
    validated = arguments.model_dump(mode="json") if arguments is not None else None
    before = budget.clock()
    try:
        timeout_ms = max(1, min(5000, int(seconds * 1000)))
        session.execute(
            text(
                "SELECT set_config('statement_timeout', :timeout, true), "
                "set_config('lock_timeout', :timeout, true)"
            ),
            {"timeout": str(timeout_ms)},
        )
        result = _execute(name, arguments, session, scope, len(traces) + 1)
    except Exception as exc:
        code = "database_error" if isinstance(exc, SQLAlchemyError) else "tool_error"
        add_trace(
            traces,
            "tool_result",
            now,
            step=step,
            call_id=call_id,
            tool_name=name,
            arguments=validated,
            error_code=code,
            duration_ms=max(0, int((budget.clock() - before) * 1000)),
        )
        if isinstance(exc, SQLAlchemyError):
            raise
        raise ExecutionFailure(code) from None
    add_trace(
        traces,
        "tool_result",
        now,
        step=step,
        call_id=call_id,
        tool_name=name,
        arguments=result.arguments,
        observation=result.observation,
        error_code=result.observation["error_code"],
        duration_ms=max(0, int((budget.clock() - before) * 1000)),
    )
    budget.remaining()
    return result
