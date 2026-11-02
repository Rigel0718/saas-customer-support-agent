from datetime import datetime
from typing import Literal

from pydantic import BaseModel

ThreadKind = Literal["issue", "discussion"]


class DocumentHit(BaseModel):
    path: str
    title: str
    url: str
    rank: float
    snippet: str


class DocumentDTO(BaseModel):
    path: str
    title: str
    url: str
    content: str
    truncated: bool


class ThreadHit(BaseModel):
    kind: ThreadKind
    number: int
    title: str
    state: Literal["open", "closed"]
    created_at: datetime
    rank: float
    snippet: str


class PostDTO(BaseModel):
    post_id: str
    parent_post_id: str | None
    body: str
    author_association: str
    created_at: datetime
    is_answer: bool


class ThreadDTO(BaseModel):
    kind: ThreadKind
    number: int
    title: str
    body: str
    url: str
    category: str | None
    # as_of 기준으로 다시 계산한 값이다. 라벨은 붙은 시점을 알 수 없어 제공하지 않는다.
    state: Literal["open", "closed"]
    state_reason: str | None
    created_at: datetime
    closed_at: datetime | None
    has_answer: bool
    posts: list[PostDTO]
    truncated_posts: bool


class DocumentSearchDTO(BaseModel):
    items: list[DocumentHit]


class ThreadSearchDTO(BaseModel):
    items: list[ThreadHit]
