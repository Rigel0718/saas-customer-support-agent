"""GitHub REST/GraphQL collection for the support knowledge base.

The token is only sent as a header. It never appears in records, errors or logs.
"""

import re
import time
from collections.abc import Callable, Iterator

import httpx

API = "https://api.github.com"
MAX_RETRIES = 3
DISCUSSION_POSTS = 50
DISCUSSION_REPLIES = 20
NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')


class CollectionError(Exception):
    """Only a safe code crosses this boundary; never response bodies or headers."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class GitHubClient:
    def __init__(
        self,
        token: str | None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(
            base_url=API,
            headers=headers,
            transport=transport,
            timeout=30,
            follow_redirects=True,
        )
        self._sleep = sleep
        self._clock = clock

    def close(self) -> None:
        self._http.close()

    def request(
        self, method: str, url: str, *, authenticated: bool = True, **kwargs
    ) -> httpx.Response:
        for attempt in range(MAX_RETRIES + 1):
            request = self._http.build_request(method, url, **kwargs)
            if not authenticated:
                # 공개 파일 호스트에는 토큰을 보내지 않는다.
                request.headers.pop("Authorization", None)
            try:
                response = self._http.send(request)
            except httpx.HTTPError:
                raise CollectionError("network_error") from None
            # 1차 한도(remaining=0)와 2차 한도(403 + Retry-After)를 모두 재시도한다.
            limited = response.status_code == 429 or (
                response.status_code == 403
                and (
                    response.headers.get("x-ratelimit-remaining") == "0"
                    or "retry-after" in response.headers
                )
            )
            if limited and attempt < MAX_RETRIES:
                self._sleep(self._wait_seconds(response))
                continue
            if limited:
                raise CollectionError("rate_limited")
            if response.status_code >= 400:
                raise CollectionError(f"http_{response.status_code}")
            return response
        raise CollectionError("rate_limited")

    def _wait_seconds(self, response: httpx.Response) -> float:
        retry_after = response.headers.get("retry-after")
        if retry_after and retry_after.isdigit():
            return float(retry_after)
        reset = response.headers.get("x-ratelimit-reset")
        if reset and reset.isdigit():
            return max(1.0, float(reset) - self._clock() + 1)
        return 60.0

    def paginate(self, path: str, params: dict) -> Iterator[dict]:
        url, query = path, {**params, "per_page": 100}
        while url:
            response = self.request("GET", url, params=query)
            yield from response.json()
            match = NEXT_LINK.search(response.headers.get("link", ""))
            url, query = (match.group(1), None) if match else (None, None)

    def graphql(self, query: str, variables: dict) -> dict:
        data = self.request(
            "POST", "/graphql", json={"query": query, "variables": variables}
        ).json()
        if data.get("errors") or "data" not in data:
            raise CollectionError("graphql_error")
        return data["data"]


def collect_issues(client: GitHubClient, repo: str) -> tuple[list[dict], list[dict]]:
    """Return issue threads and their comments. Pull requests are excluded."""
    threads = []
    for item in client.paginate(
        f"/repos/{repo}/issues", {"state": "all", "sort": "created", "direction": "asc"}
    ):
        if "pull_request" in item:
            continue
        threads.append(
            {
                "kind": "issue",
                "number": item["number"],
                "title": item["title"],
                "body": item.get("body") or "",
                "url": item["html_url"],
                "category": None,
                "labels": sorted(label["name"] for label in item.get("labels", [])),
                "state": item["state"],
                "state_reason": item.get("state_reason"),
                "created_at": item["created_at"],
                "closed_at": item.get("closed_at"),
                "answer_post_id": None,
                "answer_chosen_at": None,
                "truncated_posts": False,
            }
        )
    numbers = {thread["number"] for thread in threads}
    posts = []
    for item in client.paginate(
        f"/repos/{repo}/issues/comments", {"sort": "created", "direction": "asc"}
    ):
        number = int(item["issue_url"].rsplit("/", 1)[1])
        # 저장소 단위 댓글에는 Pull Request 댓글도 섞여 있다.
        if number not in numbers:
            continue
        posts.append(
            {
                "thread_kind": "issue",
                "thread_number": number,
                "post_id": str(item["id"]),
                "parent_post_id": None,
                "body": item.get("body") or "",
                "author_association": item.get("author_association") or "NONE",
                "author_is_bot": (item.get("user") or {}).get("type") == "Bot",
                "created_at": item["created_at"],
            }
        )
    return threads, posts


DISCUSSIONS_QUERY = """
query(
  $owner: String!, $name: String!, $cursor: String, $posts: Int!, $replies: Int!
) {
  repository(owner: $owner, name: $name) {
    discussions(
      first: 30, after: $cursor
      orderBy: {field: CREATED_AT, direction: ASC}
    ) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title body url createdAt closed closedAt stateReason answerChosenAt
        category { name }
        labels(first: 20) { nodes { name } }
        answer { id }
        comments(first: $posts) {
          totalCount
          nodes {
            id body authorAssociation createdAt author { __typename }
            replies(first: $replies) {
              totalCount
              nodes { id body authorAssociation createdAt author { __typename } }
            }
          }
        }
      }
    }
  }
}
"""


def collect_discussions(
    client: GitHubClient, repo: str
) -> tuple[list[dict], list[dict]]:
    owner, name = repo.split("/", 1)
    threads, posts, cursor = [], [], None
    while True:
        page = client.graphql(
            DISCUSSIONS_QUERY,
            {
                "owner": owner,
                "name": name,
                "cursor": cursor,
                "posts": DISCUSSION_POSTS,
                "replies": DISCUSSION_REPLIES,
            },
        )["repository"]["discussions"]
        for node in page["nodes"]:
            comments = node["comments"]
            replies_truncated = any(
                c["replies"]["totalCount"] > len(c["replies"]["nodes"])
                for c in comments["nodes"]
            )
            threads.append(
                {
                    "kind": "discussion",
                    "number": node["number"],
                    "title": node["title"],
                    "body": node.get("body") or "",
                    "url": node["url"],
                    "category": (node.get("category") or {}).get("name"),
                    "labels": sorted(
                        label["name"] for label in node["labels"]["nodes"]
                    ),
                    "state": "closed" if node["closed"] else "open",
                    "state_reason": (node.get("stateReason") or "").lower() or None,
                    "created_at": node["createdAt"],
                    "closed_at": node.get("closedAt"),
                    "answer_post_id": (node.get("answer") or {}).get("id"),
                    "answer_chosen_at": node.get("answerChosenAt"),
                    "truncated_posts": comments["totalCount"] > len(comments["nodes"])
                    or replies_truncated,
                }
            )
            for comment in comments["nodes"]:
                posts.append(_discussion_post(node["number"], comment, None))
                posts.extend(
                    _discussion_post(node["number"], reply, comment["id"])
                    for reply in comment["replies"]["nodes"]
                )
        if not page["pageInfo"]["hasNextPage"]:
            return threads, posts
        cursor = page["pageInfo"]["endCursor"]


def _discussion_post(number: int, comment: dict, parent: str | None) -> dict:
    return {
        "thread_kind": "discussion",
        "thread_number": number,
        "post_id": comment["id"],
        "parent_post_id": parent,
        "body": comment.get("body") or "",
        "author_association": comment.get("authorAssociation") or "NONE",
        # 삭제된 계정(author=null)은 봇이 아닌 것으로 본다.
        "author_is_bot": (comment.get("author") or {}).get("__typename") == "Bot",
        "created_at": comment["createdAt"],
    }
