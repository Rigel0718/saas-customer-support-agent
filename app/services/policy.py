import json
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints


class PolicyQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]


class PolicyDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    title: str
    keywords: list[str]
    content: str


POLICY_DIR = Path(__file__).resolve().parents[2] / "policies"


def load_policies(directory: Path = POLICY_DIR) -> list[PolicyDocument]:
    documents = [
        PolicyDocument.model_validate(
            json.loads((directory / name).read_text(encoding="utf-8"))
        )
        for name in ("plans.json", "permissions.json", "troubleshooting.json")
    ]
    if len({doc.document_id for doc in documents}) != len(documents):
        raise ValueError("Duplicate policy identifier")
    return documents


def search_policy(query: str, documents: list[PolicyDocument] | None = None) -> dict:
    query = PolicyQuery(query=query).query
    documents = documents if documents is not None else load_policies()
    tokens = query.casefold().split()
    scored = []
    for doc in documents:
        corpus = " ".join([doc.title, *doc.keywords, doc.content]).casefold()
        score = sum(token in corpus for token in tokens)
        if score:
            scored.append((score, doc))
    scored.sort(key=lambda pair: (-pair[0], pair[1].document_id))
    return {
        "items": [
            {"document_id": doc.document_id, "title": doc.title, "content": doc.content}
            for _, doc in scored[:3]
        ]
    }
