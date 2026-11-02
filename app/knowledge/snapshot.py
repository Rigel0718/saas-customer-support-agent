"""Versioned on-disk snapshots: manifest + JSONL, never overwritten."""

import hashlib
import json
import re
from pathlib import Path

FILES = ("documents", "threads", "posts")


def next_snapshot_id(root: Path, source: str, date: str) -> str:
    prefix = f"{source}-{date}-v"
    pattern = re.compile(re.escape(prefix) + r"(\d+)$")
    directory = root / source
    versions = [
        int(match.group(1))
        for path in (directory.iterdir() if directory.exists() else [])
        if (match := pattern.match(path.name))
    ]
    return f"{prefix}{max(versions, default=0) + 1}"


def write_jsonl(path: Path, records: list[dict]) -> dict:
    data = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    ).encode("utf-8")
    path.write_bytes(data)
    return {"count": len(records), "sha256": hashlib.sha256(data).hexdigest()}


def write_snapshot(
    directory: Path, manifest: dict, data: dict[str, list[dict]]
) -> dict:
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {
        **manifest,
        "files": {
            name: write_jsonl(directory / f"{name}.jsonl", data.get(name, []))
            for name in FILES
        },
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def read_snapshot(directory: Path) -> tuple[dict, dict[str, list[dict]]]:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    data = {}
    for name in FILES:
        raw = (directory / f"{name}.jsonl").read_bytes()
        expected = manifest["files"][name]
        if hashlib.sha256(raw).hexdigest() != expected["sha256"]:
            raise ValueError(f"{name}.jsonl does not match manifest")
        # splitlines()는 본문 속 U+2028 등도 줄바꿈으로 보므로 \n으로만 나눈다.
        data[name] = [
            json.loads(line) for line in raw.decode("utf-8").split("\n") if line
        ]
    return manifest, data
