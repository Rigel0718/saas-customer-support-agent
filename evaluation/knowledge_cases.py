"""Evaluation case candidates from resolved support threads in [T0, T1].

Final case selection, human review and GT freezing happen in the experiment step.
"""

import re
from datetime import date, datetime, timezone

from app.knowledge.sources import GitHubSource

DUPLICATE_OF = re.compile(r"duplicate of\s+(?:[\w.-]+/[\w.-]+)?#(\d+)", re.IGNORECASE)
HASH_REFERENCE = re.compile(r"(?<![\w/&])#(\d+)\b")


def thread_references(source: GitHubSource, text: str) -> list[int]:
    url = re.compile(
        rf"https?://github\.com/{re.escape(source.threads_repo)}/(?:issues|discussions)/(\d+)"
    )
    numbers = {int(n) for n in url.findall(text)}
    numbers |= {int(n) for n in HASH_REFERENCE.findall(url.sub("", text))}
    return sorted(numbers)


def document_references(source: GitHubSource, text: str) -> list[str]:
    """Site paths linked in the text (`/docs/...`, `/self-hosting/...` 등 모든 경로).

    스냅샷에 없는 경로는 호출자가 missing으로 표시한다.
    """
    site = re.escape(source.docs_site)
    # 언어 접두사(/ko/ 등), 앵커, 쿼리, 끝 슬래시는 문서 경로에서 뺀다.
    pattern = re.compile(rf"{site}(?:/[a-z]{{2}}(?=/))?/([\w][\w/.-]*)")
    return sorted({match.rstrip("/.") for match in pattern.findall(text)})


def visible_document_paths(documents: list[dict], as_of: datetime) -> set[str]:
    """질문 시점에 지식 베이스가 보여 주는 문서 경로(검색 Service와 같은 규칙)."""
    versions = [
        datetime.fromisoformat(d["valid_from"])
        for d in documents
        if d["published_on"] is None
        and datetime.fromisoformat(d["valid_from"]) <= as_of
    ]
    current = max(versions, default=None)
    day = as_of.astimezone(timezone.utc).date()
    return {
        d["path"]
        for d in documents
        if (
            d["published_on"] is None
            and current is not None
            and datetime.fromisoformat(d["valid_from"]) == current
        )
        or (
            d["published_on"] is not None
            and date.fromisoformat(d["published_on"]) < day
        )
    }


def _in_window(value: str, t0: datetime, t1: datetime) -> bool:
    return t0 <= datetime.fromisoformat(value) <= t1


def extract_candidates(
    source: GitHubSource,
    data: dict[str, list[dict]],
    t0: datetime,
    t1: datetime,
) -> list[dict]:
    posts: dict[tuple[str, int], list[dict]] = {}
    for post in data["posts"]:
        posts.setdefault((post["thread_kind"], post["thread_number"]), []).append(post)
    candidates = []
    for thread in data["threads"]:
        if not _in_window(thread["created_at"], t0, t1):
            continue
        key = (thread["kind"], thread["number"])
        base = {
            "case_id": f"{thread['kind']}-{thread['number']}",
            "kind": thread["kind"],
            "number": thread["number"],
            "created_at": thread["created_at"],
            "title": thread["title"],
            "body": thread["body"],
        }
        if thread["kind"] == "discussion" and thread["answer_post_id"]:
            answer = next(
                (
                    p
                    for p in posts.get(key, [])
                    if p["post_id"] == thread["answer_post_id"]
                ),
                None,
            )
            # 본문을 수집하지 못한 답변은 근거가 없고, 봇 답변은 GT로 쓰지 않는다.
            if answer is None or answer["author_is_bot"]:
                continue
            documents = document_references(source, answer["body"])
            document_paths = visible_document_paths(
                data["documents"], datetime.fromisoformat(thread["created_at"])
            )
            candidates.append(
                {
                    **base,
                    "type": "answered_discussion",
                    "answer": answer["body"],
                    "answer_author_association": answer["author_association"],
                    "answer_created_at": answer["created_at"],
                    "referenced_documents": documents,
                    "missing_documents": [
                        path for path in documents if path not in document_paths
                    ],
                    "referenced_threads": [
                        number
                        for number in thread_references(source, answer["body"])
                        if number != thread["number"]
                    ],
                    "duplicate_of": None,
                }
            )
        elif thread["kind"] == "issue" and thread["state_reason"] == "duplicate":
            texts = [thread["body"]] + [p["body"] for p in posts.get(key, [])]
            original = next(
                (
                    int(match.group(1))
                    for text in texts
                    if (match := DUPLICATE_OF.search(text))
                    and int(match.group(1)) != thread["number"]
                ),
                None,
            )
            if original is None:
                continue
            candidates.append(
                {
                    **base,
                    "type": "duplicate_issue",
                    "answer": None,
                    "answer_author_association": None,
                    "answer_created_at": None,
                    "referenced_documents": [],
                    "missing_documents": [],
                    "referenced_threads": [original],
                    "duplicate_of": original,
                }
            )
    candidates.sort(
        key=lambda candidate: (candidate["created_at"], candidate["case_id"])
    )
    return candidates
