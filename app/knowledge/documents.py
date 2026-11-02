"""Product documents fixed to the last docs commit before T0.

Only Markdown/MDX files under the docs roots are fetched one by one; the rest of the
repository (images and other assets) is never downloaded. File contents are data only.
"""

import re
from datetime import date, datetime, timezone
from pathlib import PurePosixPath

from app.knowledge.github import CollectionError, GitHubClient
from app.knowledge.sources import GitHubSource

RAW = "https://raw.githubusercontent.com"
DOCUMENT_SUFFIXES = (".md", ".mdx")
FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
FRONTMATTER_TITLE = re.compile(r"^title:\s*[\"']?(.+?)[\"']?\s*$", re.MULTILINE)
MODULE_LINE = re.compile(r"^(import|export)\s.*$", re.MULTILINE)
HEADING = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
DATED_PATH = re.compile(r"(?:^|/)(\d{4}-\d{2}-\d{2})-")
LATEST = "latest"


def published_on(path: str) -> date | None:
    match = DATED_PATH.search(path)
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def quarter_boundaries(t0: datetime, until: datetime) -> list[datetime]:
    """T0와, T0 이후 until 이전의 분기 시작 시각(UTC)."""
    boundaries = [t0]
    year, month = t0.year, ((t0.month - 1) // 3) * 3 + 1
    while True:
        month += 3
        if month > 12:
            year, month = year + 1, month - 12
        boundary = datetime(year, month, 1, tzinfo=timezone.utc)
        if boundary >= until:
            return boundaries
        if boundary > t0:
            boundaries.append(boundary)


def commit_before(client: GitHubClient, repo: str, t0: datetime) -> str:
    commits = client.request(
        "GET",
        f"/repos/{repo}/commits",
        params={"until": t0.isoformat(), "per_page": 1},
    ).json()
    if not commits:
        raise CollectionError("docs_commit_not_found")
    return commits[0]["sha"]


def site_path(source: GitHubSource, relative: str) -> str | None:
    """`pages/docs/tracing/index.mdx` -> `docs/tracing` (사이트 URL 경로)."""
    if relative.startswith("/") or ".." in PurePosixPath(relative).parts:
        return None
    if not relative.endswith(DOCUMENT_SUFFIXES):
        return None
    for root in source.docs_roots:
        if relative.startswith(root):
            path = relative[len(root) :]
            break
    else:
        return None
    path = path.rsplit(".", 1)[0]
    if path == "index":
        return ""
    return path.removesuffix("/index")


def document_files(
    client: GitHubClient, source: GitHubSource, sha: str
) -> list[tuple[str, str]]:
    """Return (repository path, site path) of docs files at the commit."""
    tree = client.request(
        "GET", f"/repos/{source.docs_repo}/git/trees/{sha}", params={"recursive": 1}
    ).json()
    if tree.get("truncated"):
        raise CollectionError("docs_tree_truncated")
    files, seen = [], set()
    # `x.mdx`와 `x/index.mdx`처럼 사이트 경로가 겹치면
    # 경로 이름순으로 먼저 나온 것을 쓴다.
    for entry in sorted(tree["tree"], key=lambda entry: entry["path"]):
        path = site_path(source, entry["path"]) if entry["type"] == "blob" else None
        if path is None or path in seen:
            continue
        seen.add(path)
        files.append((entry["path"], path))
    return files


def clean_document(raw: str, fallback_title: str) -> tuple[str, str]:
    title = None
    match = FRONTMATTER.match(raw)
    if match:
        found = FRONTMATTER_TITLE.search(match.group(1))
        title = found.group(1) if found else None
        raw = raw[match.end() :]
    content = MODULE_LINE.sub("", raw).strip()
    if title is None:
        heading = HEADING.search(content)
        title = heading.group(1) if heading else fallback_title
    return title, content


def collect_documents(
    client: GitHubClient,
    source: GitHubSource,
    sha: str,
    *,
    version: str,
    valid_from: datetime,
    dated: bool,
) -> list[dict]:
    """Fetch undated docs (dated=False) or dated docs (dated=True) at the commit."""
    documents = []
    for repo_path, path in document_files(client, source, sha):
        published = published_on(path)
        if (published is not None) != dated:
            continue
        raw = client.request(
            "GET", f"{RAW}/{source.docs_repo}/{sha}/{repo_path}", authenticated=False
        )
        title, content = clean_document(
            raw.content.decode("utf-8", errors="replace"),
            PurePosixPath(repo_path).stem,
        )
        if not content:
            continue
        documents.append(
            {
                "version": version,
                "valid_from": valid_from.isoformat(),
                "published_on": published.isoformat() if published else None,
                "path": path,
                "title": title,
                "url": f"{source.docs_site}/{path}".rstrip("/"),
                "content": content,
            }
        )
    return documents


def collect_document_versions(
    client: GitHubClient, source: GitHubSource, t0: datetime, collected_at: datetime
) -> tuple[list[dict], dict[str, str]]:
    """Quarterly versions of undated docs plus dated docs from the latest commit."""
    documents, commits = [], {}
    for boundary in quarter_boundaries(t0, collected_at):
        version = boundary.date().isoformat()
        commits[version] = commit_before(client, source.docs_repo, boundary)
        found = collect_documents(
            client,
            source,
            commits[version],
            version=version,
            valid_from=boundary,
            dated=False,
        )
        # 문서 루트가 그 시점의 저장소 구조와 맞지 않을 때 빈 버전을 남기지 않는다.
        if not found:
            raise CollectionError("docs_not_found")
        documents += found
    commits[LATEST] = commit_before(client, source.docs_repo, collected_at)
    documents += collect_documents(
        client,
        source,
        commits[LATEST],
        version=LATEST,
        valid_from=collected_at,
        dated=True,
    )
    return documents, commits
