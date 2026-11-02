from dataclasses import dataclass, field
from typing import Annotated, Literal

from pydantic import Field

from app.schemas.support import FailureMetadata, Nonempty, Schema, TraceDTO


class Citation(Schema):
    trace_sequence: Annotated[int, Field(strict=True, gt=0)]
    source: Literal["document", "thread"]
    ref: Nonempty


class KnowledgeAnswerDTO(Schema):
    outcome: Literal["answered", "insufficient_knowledge"]
    answer: Nonempty
    citations: list[Citation]


class KnowledgeQueriesDTO(Schema):
    # 개수와 길이는 Application이 검증해 invalid_queries로 분류한다.
    queries: list[str]


@dataclass
class KnowledgeAnswerResult:
    status: Literal["completed", "failed"]
    answer: KnowledgeAnswerDTO | None
    error_code: str | None
    failure_metadata: FailureMetadata | None
    raw_output: str | None
    traces: list[TraceDTO] = field(default_factory=list)
