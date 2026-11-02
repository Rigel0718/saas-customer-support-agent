"""Knowledge collection with a fake GitHub transport; no network."""

import json
from argparse import Namespace
from datetime import datetime, timezone

import httpx
import pytest

from app.knowledge import github
from app.knowledge.documents import (
    clean_document,
    collect_document_versions,
    collect_documents,
    quarter_boundaries,
    site_path,
)
from app.knowledge.github import CollectionError, GitHubClient
from app.knowledge.snapshot import next_snapshot_id, read_snapshot, write_snapshot
from app.knowledge.sources import SOURCES
from evaluation.knowledge_cases import (
    document_references,
    extract_candidates,
    thread_references,
)
from scripts import ingest_knowledge_base as cli

SOURCE = SOURCES["langfuse"]
TOKEN = "synthetic-token-marker"
NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


def user():
    return {"login": "someone", "email": "someone@example.test", "avatar_url": "x"}


def issue(number, **extra):
    return {
        "number": number,
        "title": f"Issue {number}",
        "body": None,
        "html_url": f"https://github.com/langfuse/langfuse/issues/{number}",
        "labels": [{"name": "bug"}],
        "state": "closed",
        "state_reason": "completed",
        "created_at": "2026-06-01T00:00:00Z",
        "closed_at": "2026-06-02T00:00:00Z",
        "user": user(),
    } | extra


def comment(id_, number, bot=False):
    return {
        "id": id_,
        "issue_url": f"https://api.github.com/repos/langfuse/langfuse/issues/{number}",
        "body": f"comment {id_}",
        "author_association": "MEMBER",
        "created_at": "2026-06-01T01:00:00Z",
        "user": user() | {"type": "Bot" if bot else "User"},
    }


def node(id_, bot=False, replies=(), reply_total=None):
    return {
        "id": id_,
        "body": f"reply {id_}",
        "authorAssociation": "NONE",
        "createdAt": "2026-06-03T01:00:00Z",
        "author": user() | {"__typename": "Bot" if bot else "User"},
        "replies": {
            "totalCount": reply_total if reply_total is not None else len(replies),
            "nodes": [
                {k: v for k, v in node(r).items() if k != "replies"} for r in replies
            ],
        },
    }


def discussion(number, comments, total=None, answer=None):
    return {
        "number": number,
        "title": f"Discussion {number}",
        "body": "How do I configure tracing?",
        "url": f"https://github.com/langfuse/langfuse/discussions/{number}",
        "createdAt": "2026-06-03T00:00:00Z",
        "closed": False,
        "closedAt": None,
        "stateReason": None,
        "answerChosenAt": "2026-06-04T00:00:00Z" if answer else None,
        "category": {"name": "Support"},
        "labels": {"nodes": []},
        "answer": {"id": answer} if answer else None,
        "author": user(),
        "comments": {
            "totalCount": total if total is not None else len(comments),
            "nodes": comments,
        },
    }


DOCS = {
    "pages/docs/tracing.mdx": (
        "---\ntitle: Tracing Guide\n---\nimport X from 'y'\n\n"
        "# Ignored\nBody text\nexport const z = 1\n"
    ),
    "pages/docs/tracing/index.mdx": "# Duplicate path\nShould be skipped",
    "pages/docs/self-host/index.md": "# Self host\nUse clickhouse.",
    "pages/docs/empty.mdx": "---\ntitle: Empty\n---\nimport X from 'y'\n",
    "pages/index.mdx": "# Home\nWelcome",
    "README.md": "# Repo readme",
    "pages/logo.png": "binary",
    "pages/changelog/2026-06-01-launch.mdx": "# Launch\nNew monitors",
}


def docs_sha(until: str) -> str:
    # 분기 경계마다 다른 commit을 돌려준다.
    if until < "2026-07-01":
        return "sha-a"
    return "sha-b" if until < "2026-10-01" else "sha-c"


class FakeGitHub:
    def __init__(
        self,
        *,
        rate_limited=0,
        secondary_limited=0,
        fail_graphql=False,
        docs=DOCS,
        tree_extra=None,
    ):
        self.rate_limited = rate_limited
        self.secondary_limited = secondary_limited
        self.fail_graphql = fail_graphql
        self.docs = docs
        self.tree_extra = tree_extra or {}
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, page = request.url.path, request.url.params.get("page")
        if self.rate_limited:
            self.rate_limited -= 1
            return httpx.Response(
                403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "100"}
            )
        if self.secondary_limited:
            self.secondary_limited -= 1
            return httpx.Response(403, headers={"retry-after": "7"})
        if request.url.host == "raw.githubusercontent.com":
            name = path.split("/", 4)[4]
            return httpx.Response(200, content=self.docs[name].encode())
        if path.endswith("/issues/comments"):
            return httpx.Response(
                200,
                json=[
                    comment(11, 1),
                    comment(12, 2),
                    comment(13, 3),
                    comment(14, 1, bot=True),
                ],
            )
        if path.endswith("/issues"):
            if page == "2":
                return httpx.Response(200, json=[issue(3)])
            return httpx.Response(
                200,
                json=[issue(1), issue(2, pull_request={"url": "x"})],
                headers={
                    "link": f'<{request.url.copy_merge_params({"page": "2"})}>; rel="next"'
                },
            )
        if path == "/graphql":
            if self.fail_graphql:
                return httpx.Response(502, text=f"bad gateway {TOKEN}")
            cursor = json.loads(request.content)["variables"]["cursor"]
            nodes = (
                [
                    discussion(
                        10,
                        [node("c1"), node("c2", replies=["r1"]), node("c5", bot=True)],
                        answer="r1",
                    )
                ]
                if cursor is None
                else [
                    discussion(11, [node("c3")], total=80),
                    discussion(12, [node("c4", replies=["r3"], reply_total=25)]),
                ]
            )
            return httpx.Response(
                200,
                json={
                    "data": {
                        "repository": {
                            "discussions": {
                                "pageInfo": {
                                    "hasNextPage": cursor is None,
                                    "endCursor": "next" if cursor is None else None,
                                },
                                "nodes": nodes,
                            }
                        }
                    }
                },
            )
        if path.endswith("/commits"):
            return httpx.Response(
                200, json=[{"sha": docs_sha(request.url.params["until"])}]
            )
        if "/git/trees/" in path:
            entries = [{"path": name, "type": "blob"} for name in self.docs]
            entries.append({"path": "pages/docs", "type": "tree"})
            return httpx.Response(200, json={"tree": entries} | self.tree_extra)
        return httpx.Response(404)


def client(fake, sleeps=None):
    return GitHubClient(
        TOKEN,
        transport=httpx.MockTransport(fake),
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
        clock=lambda: 0,
    )


def test_issues_skip_pull_requests_and_keep_only_association():
    fake = FakeGitHub()
    threads, posts = github.collect_issues(client(fake), SOURCE.threads_repo)
    assert [t["number"] for t in threads] == [1, 3]
    assert [(p["post_id"], p["author_is_bot"]) for p in posts] == [
        ("11", False),
        ("13", False),
        ("14", True),
    ]
    assert fake.requests[0].headers["authorization"] == f"Bearer {TOKEN}"
    dumped = json.dumps([threads, posts])
    assert "someone" not in dumped and "avatar" not in dumped
    assert threads[0]["body"] == "" and threads[0]["labels"] == ["bug"]


def test_discussions_pages_answers_and_truncation():
    threads, posts = github.collect_discussions(
        client(FakeGitHub()), SOURCE.threads_repo
    )
    assert [t["number"] for t in threads] == [10, 11, 12]
    assert threads[0]["answer_post_id"] == "r1" and threads[0]["category"] == "Support"
    assert [t["truncated_posts"] for t in threads] == [False, True, True]
    assert [(p["post_id"], p["parent_post_id"], p["author_is_bot"]) for p in posts] == [
        ("c1", None, False),
        ("c2", None, False),
        ("r1", "c2", False),
        ("c5", None, True),
        ("c3", None, False),
        ("c4", None, False),
        ("r3", "c4", False),
    ]
    assert "someone" not in json.dumps([threads, posts])


def test_rate_limit_retries_then_fails_without_leaking_token():
    sleeps = []
    threads, _ = github.collect_issues(
        client(FakeGitHub(rate_limited=2), sleeps), SOURCE.threads_repo
    )
    assert len(threads) == 2 and sleeps == [101.0, 101.0]
    sleeps = []
    with pytest.raises(CollectionError) as exc:
        github.collect_issues(
            client(FakeGitHub(rate_limited=10), sleeps), SOURCE.threads_repo
        )
    assert exc.value.code == "rate_limited" and len(sleeps) == github.MAX_RETRIES
    with pytest.raises(CollectionError) as exc:
        github.collect_discussions(
            client(FakeGitHub(fail_graphql=True)), SOURCE.threads_repo
        )
    assert exc.value.code == "http_502" and TOKEN not in str(exc.value)


def test_secondary_rate_limit_is_retried():
    sleeps = []
    threads, _ = github.collect_issues(
        client(FakeGitHub(secondary_limited=1), sleeps), SOURCE.threads_repo
    )
    assert len(threads) == 2 and sleeps == [7.0]


def test_documents_fetch_only_docs_files_without_token():
    fake = FakeGitHub()
    documents = collect_documents(
        client(fake), SOURCE, "sha-a", version="v1", valid_from=NOW, dated=False
    )
    by_path = {d["path"]: d for d in documents}
    assert sorted(by_path) == ["", "docs/self-host", "docs/tracing"]
    tracing = by_path["docs/tracing"]
    assert (
        tracing["title"] == "Tracing Guide"
        and tracing["content"] == "# Ignored\nBody text"
    )
    assert tracing["url"] == "https://langfuse.com/docs/tracing"
    assert tracing["version"] == "v1" and tracing["published_on"] is None
    assert by_path["docs/self-host"]["title"] == "Self host"
    raw = [r for r in fake.requests if r.url.host == "raw.githubusercontent.com"]
    # 이미지·README·겹치는 경로·날짜 있는 문서는 받지 않고, 토큰도 보내지 않는다.
    assert sorted(r.url.path.split("/", 4)[4] for r in raw) == [
        "pages/docs/empty.mdx",
        "pages/docs/self-host/index.md",
        "pages/docs/tracing.mdx",
        "pages/index.mdx",
    ]
    assert all("authorization" not in r.headers for r in raw)
    dated = collect_documents(
        client(FakeGitHub()),
        SOURCE,
        "sha-c",
        version="latest",
        valid_from=NOW,
        dated=True,
    )
    assert [(d["path"], d["published_on"]) for d in dated] == [
        ("changelog/2026-06-01-launch", "2026-06-01")
    ]
    assert site_path(SOURCE, "README.md") is None
    assert site_path(SOURCE, "pages/../secret.mdx") is None
    assert clean_document("plain text", "fallback") == ("fallback", "plain text")


def test_document_versions_follow_quarters():
    t0 = datetime(2026, 5, 5, tzinfo=timezone.utc)
    assert [b.date().isoformat() for b in quarter_boundaries(t0, NOW)] == [
        "2026-05-05",
        "2026-07-01",
        "2026-10-01",
    ]
    documents, commits = collect_document_versions(
        client(FakeGitHub()), SOURCE, t0, NOW
    )
    assert commits == {
        "2026-05-05": "sha-a",
        "2026-07-01": "sha-b",
        "2026-10-01": "sha-c",
        "latest": "sha-c",
    }
    versions = [(d["version"], d["path"]) for d in documents]
    assert len(versions) == 3 * 3 + 1
    assert ("latest", "changelog/2026-06-01-launch") in versions
    assert {d["valid_from"] for d in documents if d["version"] == "2026-07-01"} == {
        "2026-07-01T00:00:00+00:00"
    }


@pytest.mark.parametrize(
    "fake,code",
    [
        (FakeGitHub(docs={"README.md": "# readme"}), "docs_not_found"),
        (FakeGitHub(tree_extra={"truncated": True}), "docs_tree_truncated"),
    ],
)
def test_documents_fail_instead_of_silently_empty(fake, code):
    with pytest.raises(CollectionError) as exc:
        collect_document_versions(
            client(fake), SOURCE, datetime(2026, 5, 5, tzinfo=timezone.utc), NOW
        )
    assert exc.value.code == code


def test_snapshot_versions_hashes_and_no_overwrite(tmp_path):
    root = tmp_path / "kb"
    assert next_snapshot_id(root, "langfuse", "20261101") == "langfuse-20261101-v1"
    directory = root / "langfuse" / "langfuse-20261101-v1"
    manifest = write_snapshot(
        directory,
        {"status": "complete"},
        {"threads": [{"a": 1}], "posts": [{"body": "line\u2028sep\x85next\r\nend"}]},
    )
    assert manifest["files"]["threads"]["count"] == 1
    assert next_snapshot_id(root, "langfuse", "20261101") == "langfuse-20261101-v2"
    with pytest.raises(FileExistsError):
        write_snapshot(directory, {}, {})
    assert read_snapshot(directory)[1]["threads"] == [{"a": 1}]
    assert read_snapshot(directory)[1]["posts"] == [
        {"body": "line\u2028sep\x85next\r\nend"}
    ]
    (directory / "threads.jsonl").write_text('{"a": 2}\n')
    with pytest.raises(ValueError):
        read_snapshot(directory)


def test_references_are_normalized():
    text = (
        "See https://langfuse.com/docs/tracing/overview#otel and "
        "https://langfuse.com/ko/docs/self-host/ plus #12, "
        "https://langfuse.com/self-hosting/configuration?x=1, "
        "https://github.com/langfuse/langfuse/discussions/34 and "
        "https://github.com/orgs/langfuse/discussions/56#discussioncomment-1 and "
        "https://github.com/other/repo/issues/99 and color #fff"
    )
    assert document_references(SOURCE, text) == [
        "docs/self-host",
        "docs/tracing/overview",
        "self-hosting/configuration",
    ]
    assert thread_references(SOURCE, text) == [12, 34, 56]


def candidate_data():
    def thread(kind, number, created, **extra):
        return {
            "kind": kind,
            "number": number,
            "title": "t",
            "body": "",
            "created_at": created,
            "answer_post_id": None,
            "state_reason": None,
        } | extra

    def post(kind, number, post_id, body, bot=False, parent=None):
        return {
            "thread_kind": kind,
            "thread_number": number,
            "post_id": post_id,
            "parent_post_id": parent,
            "body": body,
            "author_association": "MEMBER",
            "author_is_bot": bot,
            "created_at": "2026-07-02T00:00:00+00:00",
        }

    def document(path, valid_from, published=None):
        return {
            "version": valid_from[:10],
            "valid_from": valid_from,
            "published_on": published,
            "path": path,
        }

    day = "2026-07-01T00:00:00+00:00"
    return {
        "documents": [
            document("docs/tracing", "2026-05-01T00:00:00+00:00"),
            # 질문 이후 버전에만 있는 문서는 질문 시점 기준으로 없는 문서다.
            document("docs/new-feature", "2026-08-01T00:00:00+00:00"),
            document(
                "changelog/2026-06-30-launch", "2026-10-01T00:00:00+00:00", "2026-06-30"
            ),
        ],
        "threads": [
            thread("discussion", 1, day, answer_post_id="a1"),
            thread("discussion", 2, day),
            thread("discussion", 3, "2025-01-01T00:00:00+00:00", answer_post_id="a3"),
            thread("issue", 4, day, state_reason="duplicate", body="Duplicate of #40"),
            thread("issue", 5, day, state_reason="duplicate"),
            thread("discussion", 6, day, answer_post_id="missing"),
            thread("discussion", 7, day, answer_post_id="bot"),
            thread("discussion", 8, day, answer_post_id="reply"),
        ],
        "posts": [
            post(
                "discussion",
                1,
                "a1",
                "Read https://langfuse.com/docs/tracing, "
                "https://langfuse.com/changelog/2026-06-30-launch and "
                "https://langfuse.com/docs/new-feature, see #7 and #1",
            ),
            post("discussion", 3, "a3", "old"),
            post("discussion", 7, "bot", "automated answer", bot=True),
            post("discussion", 8, "top", "question"),
            post("discussion", 8, "reply", "answer in a reply", parent="top"),
        ],
    }


def test_candidates_select_answered_and_duplicate_threads():
    found = extract_candidates(
        SOURCE,
        candidate_data(),
        datetime(2026, 5, 1, tzinfo=timezone.utc),
        datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    assert [c["case_id"] for c in found] == ["discussion-1", "discussion-8", "issue-4"]
    answered, reply, duplicate = found
    assert answered["referenced_documents"] == [
        "changelog/2026-06-30-launch",
        "docs/new-feature",
        "docs/tracing",
    ]
    assert answered["missing_documents"] == ["docs/new-feature"]
    assert answered["referenced_threads"] == [7]
    assert answered["answer_author_association"] == "MEMBER"
    assert reply["answer"] == "answer in a reply"
    assert duplicate["duplicate_of"] == 40 and duplicate["referenced_threads"] == [40]


def collect_args(tmp_path, **extra):
    return Namespace(source="langfuse", t0=None, t1=None, root=tmp_path / "kb", **extra)


def test_cli_requires_network_flag_before_anything(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["collect"]) == 2
    assert "collect_requires_allow_network" in capsys.readouterr().out
    assert not (tmp_path / ".knowledge").exists()


def test_collect_writes_complete_snapshot(tmp_path):
    directory = cli.collect(
        collect_args(tmp_path), client=client(FakeGitHub()), now=lambda: NOW
    )
    manifest, data = read_snapshot(directory)
    assert directory.name == "langfuse-20261101-v1"
    assert manifest["status"] == "complete"
    assert manifest["docs_commits"] == {
        "2026-05-05": "sha-a",
        "2026-07-01": "sha-b",
        "2026-10-01": "sha-c",
        "latest": "sha-c",
    }
    assert manifest["t0"] == "2026-05-05T00:00:00+00:00"
    assert {k: v["count"] for k, v in manifest["files"].items()} == {
        "documents": 10,
        "threads": 5,
        "posts": 10,
    }
    assert TOKEN not in "".join(p.read_text() for p in directory.iterdir())


@pytest.mark.parametrize(
    "fake,code",
    [
        (FakeGitHub(fail_graphql=True), "http_502"),
        (FakeGitHub(docs={"README.md": "# readme"}), "docs_not_found"),
        # 예상하지 못한 응답 형식도 원문 없이 incomplete로 남긴다.
        (FakeGitHub(tree_extra={"tree": "not-a-list"}), "unexpected_response"),
    ],
)
def test_collect_failure_is_incomplete_and_secret_free(tmp_path, fake, code):
    directory = cli.collect(
        collect_args(tmp_path), client=client(fake), now=lambda: NOW
    )
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["status"] == "incomplete" and manifest["error_code"] == code
    assert TOKEN not in "".join(p.read_text() for p in directory.iterdir())


def test_candidates_command_refuses_overwrite(tmp_path):
    directory = cli.collect(
        collect_args(tmp_path), client=client(FakeGitHub()), now=lambda: NOW
    )
    path = cli.candidates(Namespace(snapshot=directory))
    assert path.exists()
    with pytest.raises(FileExistsError):
        cli.candidates(Namespace(snapshot=directory))
