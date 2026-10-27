"""Versioned raw artifacts with atomic per-file replacement and strict ID links."""

import json
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from app.config import Settings
from app.schemas.support import RequestDTO, TraceDTO
from evaluation.dataset import Bundle, EvaluationError, Model, sha256

SCHEMA_VERSION = "evaluation-v1"
ROOT = Path(__file__).resolve().parents[1]
LATENCY_BOUNDARY = "monotonic: immediately before create_request through diagnose_request return/exception; includes service persistence; excludes preflight/authentication/collection/judge/files; not HTTP latency"


class Execution(Model):
    execution_id: str
    case_id: str
    mode: Literal["agent", "workflow"]
    repeat: int
    order: int
    state: Literal[
        "not_started", "completed", "failed", "incomplete", "environment_error"
    ] = "not_started"
    started: bool = False
    request_id: str | None = None
    request: RequestDTO | None = None
    traces: list[TraceDTO] = Field(default_factory=list)
    trace_complete: bool = False
    execution_success: bool | None = None
    elapsed_ms: float | None = None
    censored: bool = False
    errors: list[dict[str, str]] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)


class Manifest(Model):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    run_id: str
    reference_time: str
    started_at: str
    finished_at: str | None = None
    status: Literal[
        "prepared",
        "collecting",
        "collected",
        "invalid_environment",
        "interrupted",
        "storage_failed",
    ] = "prepared"
    db_schema: str
    repetitions: Literal[1, 5]
    plan: list[dict]
    provenance: dict
    configuration: dict
    pricing: dict
    response_models: list[str] = Field(default_factory=list)
    comparison_limitations: list[str] = Field(default_factory=list)
    latency_boundary: str = LATENCY_BOUNDARY
    completed_count: int = 0
    error_code: str | None = None
    source: dict | None = None


class Judgment(Model):
    judgment_id: str
    execution_id: str
    judge_status: Literal["not_applicable", "judged", "judge_failed"]
    semantic_verdict: Literal["correct", "incorrect", "review_required"] | None
    final_verdict: Literal[
        "correct", "incorrect", "review_required", "pending", "not_applicable"
    ]
    reason: str | None
    missing_causes: list[str]
    missing_facts: list[str]
    incorrect_causes: list[str]
    outcome_match: bool | None
    versions: dict
    response_model: str | None
    usage: dict | None
    latency_ms: float | None
    error_code: str | None
    overhead: dict | None


class JudgmentFile(Model):
    schema_version: Literal[SCHEMA_VERSION]
    run_id: str
    judgment_set_id: str
    status: Literal["ungraded", "grading", "graded", "interrupted"]
    calibration: dict
    items: list[Judgment]
    versions: dict | None = None
    pricing: dict | None = None
    prompt: str | None = None
    rubric: str | None = None


class SummaryFile(Model):
    schema_version: Literal[SCHEMA_VERSION]
    run_id: str
    judgment_set_id: str
    status: Literal["ungraded"] | None = None
    run_status: str | None = None
    valid_comparison: bool | None = None
    calibration: dict | None = None
    quality_conclusion_ready: bool | None = None
    by_mode: dict = Field(default_factory=dict)
    by_case_mode: dict = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    metrics_pricing_version: str | None = None


def code_provenance() -> dict:
    paths = sorted(
        [
            *ROOT.glob("app/**/*.py"),
            *ROOT.glob("evaluation/**/*.py"),
            ROOT / "scripts/evaluate.py",
        ]
    )
    hashes = {
        str(path.relative_to(ROOT)): sha256(path.read_bytes())
        for path in paths
        if path.exists()
    }
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL, text=True
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=ROOT, stderr=subprocess.DEVNULL
            )
        )
    except (OSError, subprocess.CalledProcessError):
        revision, dirty = None, None
    return {
        "revision": revision,
        "dirty": dirty,
        "files_sha256": hashes,
        "prompt_files": [
            "app/agent/loop.py",
            "app/workflow/runner.py",
            "app/diagnosis/tools.py",
        ],
    }


def build_manifest(
    run_id: UUID,
    reference_time: datetime,
    repetitions: int,
    plan: list[Execution],
    bundle: Bundle,
    settings: Settings,
    pricing: dict,
) -> Manifest:
    # Deliberate allowlist: never serialize Settings or environment variables.
    return Manifest(
        run_id=str(run_id),
        reference_time=reference_time.isoformat(),
        started_at=reference_time.isoformat(),
        db_schema="eval_" + run_id.hex,
        repetitions=repetitions,
        plan=[
            e.model_dump(include={"execution_id", "case_id", "mode", "repeat", "order"})
            for e in plan
        ],
        provenance={**bundle.provenance, "code": code_provenance()},
        configuration={
            "diagnosis_model": settings.openai_model,
            "agent_max_steps": settings.agent_max_steps,
            "agent_max_tool_calls": settings.agent_max_tool_calls,
            "agent_timeout_seconds": settings.agent_timeout_seconds,
            "workflow_timeout_seconds": settings.workflow_timeout_seconds,
            "llm_request_timeout_seconds": settings.llm_request_timeout_seconds,
            "workflow_tool_calls": 7,
            "workflow_llm_calls": 1,
            "mode_order": "odd: agent/workflow; even: workflow/agent",
            "odd_even_start_bias": "1:0" if repetitions == 1 else "3:2",
        },
        pricing=pricing,
        comparison_limitations=[
            "Five cases and 1/5 repetitions do not establish general superiority or statistical significance.",
            "Current service does not retain invalid raw output or responses lost before terminal persistence.",
            "Model aliases and prompts/context differ; dynamic tool selection is not isolated causally.",
        ],
    )


def atomic_json(path: Path, data: dict) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".pending-",
            delete=False,
        ) as file:
            temporary = file.name
            json.dump(data, file, ensure_ascii=False, indent=2, allow_nan=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except Exception:
        raise EvaluationError("result_storage_failed") from None
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


class ResultStore:
    def __init__(
        self,
        directory: Path,
        manifest: Manifest,
        executions: list[Execution],
        judgment_set_id: str | None = None,
    ):
        self.directory = directory
        self.manifest = manifest
        self.executions = executions
        self.judgment_set_id = judgment_set_id or str(uuid4())
        self.judgments = {
            "status": "ungraded",
            "calibration": {"status": "uncalibrated"},
            "items": [],
        }
        self.summary = {"status": "ungraded"}

    def envelope(self, data: dict) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.manifest.run_id,
            "judgment_set_id": self.judgment_set_id,
            **data,
        }

    def initialize(self) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=False)
        except OSError:
            raise EvaluationError("result_directory_exists_or_unavailable") from None
        self.save_collection()
        self.save_grading()

    def save_collection(self) -> None:
        atomic_json(
            self.directory / "executions.json",
            self.envelope(
                {"items": [e.model_dump(mode="json") for e in self.executions]}
            ),
        )
        atomic_json(
            self.directory / "manifest.json", self.manifest.model_dump(mode="json")
        )

    def save_grading(self) -> None:
        atomic_json(self.directory / "judgments.json", self.envelope(self.judgments))
        atomic_json(self.directory / "summary.json", self.envelope(self.summary))

    @classmethod
    def load(cls, directory: Path) -> "ResultStore":
        try:
            manifest = Manifest.model_validate_json(
                (directory / "manifest.json").read_bytes()
            )
            files = {
                name: json.loads((directory / f"{name}.json").read_bytes())
                for name in ("executions", "judgments", "summary")
            }
            JudgmentFile.model_validate(files["judgments"])
            SummaryFile.model_validate(files["summary"])
            set_id = files["executions"]["judgment_set_id"]
            for data in files.values():
                if (
                    data["schema_version"] != SCHEMA_VERSION
                    or data["run_id"] != manifest.run_id
                    or data["judgment_set_id"] != set_id
                ):
                    raise ValueError
            # JSON validation permits UUID/datetime conversion inside existing DTOs.
            executions = [
                Execution.model_validate_json(json.dumps(e))
                for e in files["executions"]["items"]
            ]
            planned_ids = [e["execution_id"] for e in manifest.plan]
            if (
                len(set(planned_ids)) != len(planned_ids)
                or [e.execution_id for e in executions] != planned_ids
            ):
                raise ValueError
            for entry, execution in zip(manifest.plan, executions, strict=True):
                if any(
                    getattr(execution, key) != value for key, value in entry.items()
                ):
                    raise ValueError
                if (
                    execution.request is not None
                    and str(execution.request.id) != execution.request_id
                ):
                    raise ValueError
            request_ids = [e.request_id for e in executions if e.request_id]
            if len(set(request_ids)) != len(request_ids):
                raise ValueError
            judgment_ids = [j["execution_id"] for j in files["judgments"]["items"]]
            if len(set(judgment_ids)) != len(judgment_ids) or not set(
                judgment_ids
            ) <= set(planned_ids):
                raise ValueError
            result = cls(directory, manifest, executions, set_id)
            result.judgments = {
                k: v
                for k, v in files["judgments"].items()
                if k not in ("schema_version", "run_id", "judgment_set_id")
            }
            result.summary = {
                k: v
                for k, v in files["summary"].items()
                if k not in ("schema_version", "run_id", "judgment_set_id")
            }
            return result
        except Exception:
            raise EvaluationError("invalid_result_artifacts") from None


def prepare_regrade(
    source: ResultStore,
    bundle: Bundle,
    pricing: dict,
    judgment_set_id: str | None = None,
) -> ResultStore:
    set_id = judgment_set_id or str(uuid4())
    try:
        UUID(set_id)
    except ValueError:
        raise EvaluationError("invalid_judgment_set_id") from None
    manifest = source.manifest.model_copy(deep=True)
    manifest.source = {
        "run_id": source.manifest.run_id,
        "directory": str(source.directory.resolve()),
        "sha256": {
            name: sha256((source.directory / name).read_bytes())
            for name in (
                "manifest.json",
                "executions.json",
                "judgments.json",
                "summary.json",
            )
        },
    }
    if (
        manifest.provenance["dataset"]["sha256"]
        != bundle.provenance["dataset"]["sha256"]
    ):
        raise EvaluationError("regrade_dataset_mismatch")
    if manifest.provenance["gt"]["sha256"] != bundle.provenance["gt"]["sha256"]:
        manifest.comparison_limitations.append(
            "Ground truth changed for regrading; source run retains original GT and is not directly comparable."
        )
    manifest.provenance["gt"] = bundle.provenance["gt"]
    manifest.pricing = pricing
    # Raw executions (including their original measured metrics) remain identical.
    target = ResultStore(
        source.directory / "regrades" / set_id,
        manifest,
        [e.model_copy(deep=True) for e in source.executions],
        set_id,
    )
    target.initialize()
    return target
