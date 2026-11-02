"""Support knowledge base snapshots: collect (network), load (DB), candidates (eval)."""

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.db import build_engine
from app.knowledge.documents import collect_document_versions
from app.knowledge.github import (
    CollectionError,
    GitHubClient,
    collect_discussions,
    collect_issues,
)
from app.knowledge.loader import load_snapshot
from app.knowledge.snapshot import next_snapshot_id, read_snapshot, write_snapshot
from app.knowledge.sources import SOURCES
from evaluation.knowledge_cases import extract_candidates

ROOT = Path(".knowledge")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="지원 지식 베이스 스냅샷 관리")
    commands = result.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect", help="GitHub에서 스냅샷 수집")
    collect.add_argument("--source", choices=sorted(SOURCES), default="langfuse")
    collect.add_argument("--t0", type=parse_time, help="문서 시점·Case 기간 시작")
    collect.add_argument("--t1", type=parse_time, help="Case 기간 끝")
    collect.add_argument("--root", type=Path, default=ROOT)
    collect.add_argument(
        "--allow-network", action="store_true", help="GitHub API 호출을 허용"
    )
    load = commands.add_parser("load", help="스냅샷을 DB에 적재")
    load.add_argument("snapshot", type=Path)
    load.add_argument(
        "--database-url-env",
        default="DATABASE_URL",
        help="적재할 DB URL을 담은 환경변수 이름",
    )
    candidates = commands.add_parser("candidates", help="평가 Case 후보 추출")
    candidates.add_argument("snapshot", type=Path)
    return result


def collect(args, *, client: GitHubClient | None = None, now=utc_now) -> Path:
    source = SOURCES[args.source]
    collected_at = now()
    t0 = args.t0 or collected_at - timedelta(days=180)
    t1 = args.t1 or collected_at - timedelta(days=14)
    if not t0 < t1 <= collected_at:
        raise SystemExit("t0 < t1 <= 수집 시각이어야 합니다")
    snapshot_id = next_snapshot_id(
        args.root, source.name, collected_at.strftime("%Y%m%d")
    )
    client = client or GitHubClient(os.environ.get("GITHUB_TOKEN"))
    data: dict[str, list[dict]] = {"documents": [], "threads": [], "posts": []}
    manifest = {
        "schema_version": "knowledge-snapshot-v1",
        "source": source.name,
        "snapshot_id": snapshot_id,
        "threads_repo": source.threads_repo,
        "docs_repo": source.docs_repo,
        "t0": t0.isoformat(),
        "t1": t1.isoformat(),
        "collected_at": collected_at.isoformat(),
        "docs_commits": {},
        "status": "incomplete",
        "error_code": None,
    }
    try:
        data["documents"], manifest["docs_commits"] = collect_document_versions(
            client, source, t0, collected_at
        )
        for collector in (collect_issues, collect_discussions):
            threads, posts = collector(client, source.threads_repo)
            data["threads"] += threads
            data["posts"] += posts
        manifest["status"] = "complete"
    except CollectionError as exc:
        manifest["error_code"] = exc.code
    except Exception:
        # 응답 형식이 예상과 달라도 원문 없이 incomplete 스냅샷을 남긴다.
        manifest["error_code"] = "unexpected_response"
    finally:
        client.close()
    directory = args.root / source.name / snapshot_id
    write_snapshot(directory, manifest, data)
    return directory


def load(args) -> dict:
    manifest, data = read_snapshot(args.snapshot)
    engine = build_engine(os.environ[args.database_url_env])
    try:
        with Session(engine) as session:
            counts = load_snapshot(session, manifest, data)
            session.commit()
    finally:
        engine.dispose()
    return counts


def candidates(args) -> Path:
    manifest, data = read_snapshot(args.snapshot)
    found = extract_candidates(
        SOURCES[manifest["source"]],
        data,
        datetime.fromisoformat(manifest["t0"]),
        datetime.fromisoformat(manifest["t1"]),
    )
    path = args.snapshot / "case_candidates.jsonl"
    with path.open("x", encoding="utf-8") as file:
        for candidate in found:
            file.write(json.dumps(candidate, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.command == "collect":
        # 네트워크 호출이나 디렉터리 생성 전에 허용 여부를 확인한다.
        if not args.allow_network:
            print(json.dumps({"error": "collect_requires_allow_network"}))
            return 2
        directory = collect(args)
        manifest = json.loads((directory / "manifest.json").read_text("utf-8"))
        print(
            json.dumps(
                {
                    "snapshot": str(directory),
                    "status": manifest["status"],
                    "error_code": manifest["error_code"],
                    "counts": {k: v["count"] for k, v in manifest["files"].items()},
                },
                ensure_ascii=False,
            )
        )
        return 0 if manifest["status"] == "complete" else 1
    if args.command == "load":
        print(json.dumps({"loaded": load(args)}))
        return 0
    path = candidates(args)
    print(json.dumps({"candidates": str(path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
