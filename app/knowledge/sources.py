from dataclasses import dataclass


@dataclass(frozen=True)
class GitHubSource:
    """공개 GitHub 저장소 형태의 지원 지식 출처."""

    name: str
    threads_repo: str
    docs_repo: str
    docs_site: str
    # 문서 저장소 안에서 사이트 경로가 시작되는 디렉터리(앞에서부터 처음 일치하는 것).
    docs_roots: tuple[str, ...]


SOURCES = {
    "langfuse": GitHubSource(
        name="langfuse",
        threads_repo="langfuse/langfuse",
        docs_repo="langfuse/langfuse-docs",
        docs_site="https://langfuse.com",
        docs_roots=("pages/", "content/", "src/pages/"),
    ),
}
