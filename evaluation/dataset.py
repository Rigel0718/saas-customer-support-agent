"""Five fixed invitation cases, separate ground truth, and immutable provenance."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.services.policy import POLICY_DIR, load_policies

DATA_DIR = Path(__file__).parent / "data"
CONTENT = "팀원을 초대하려고 하는데 계속 실패합니다. 무엇이 문제인지 확인해 주세요."
CASE_IDS = [f"C{i:02}" for i in range(1, 6)]
Nonempty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class EvaluationError(Exception):
    """Only a safe code crosses the evaluation boundary; never exception text/URLs."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DatasetUser(Model):
    key: Literal["requester", "owner"]
    role: Literal["owner", "admin", "member"]


class DatasetSubscription(Model):
    plan: Literal["pro"]
    status: Literal["active", "expired"]
    expires_offset_days: int


class DatasetPayment(Model):
    status: Literal["succeeded"]
    created_offset_days: Literal[-60]
    paid_offset_days: Literal[-60]


class Case(Model):
    case_id: str
    requester_role: Literal["member", "admin"]
    member_limit: int
    users: list[DatasetUser]
    subscription: DatasetSubscription
    payments: list[DatasetPayment]


class Dataset(Model):
    dataset_version: Nonempty
    content: Literal[CONTENT]
    cases: list[Case]


class GroundTruth(Model):
    case_id: str
    expected_outcome: Literal["diagnosed", "needs_information"]
    primary_causes: list[Nonempty]
    excluded_causes: list[Nonempty]
    required_facts: Annotated[list[Nonempty], Field(min_length=1)]
    policy_document_ids: Annotated[list[Nonempty], Field(min_length=1)]


class GroundTruthSet(Model):
    gt_version: Nonempty
    cases: list[GroundTruth]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def object_hash(value) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())


def json_snapshot(path: Path) -> dict:
    raw = path.read_bytes()
    return {"path": path.name, "sha256": sha256(raw), "snapshot": json.loads(raw)}


def policy_snapshot(directory: Path = POLICY_DIR) -> dict:
    documents = load_policies(directory)
    files = [json_snapshot(directory / f"{doc.document_id}.json") for doc in documents]
    return {"sha256": object_hash(files), "files": files}


@dataclass(frozen=True)
class Bundle:
    dataset: Dataset
    gt: GroundTruthSet
    provenance: dict

    def truth(self, case_id: str) -> GroundTruth:
        return next(gt for gt in self.gt.cases if gt.case_id == case_id)


def load_bundle(
    cases_path: Path = DATA_DIR / "invite_cases.json",
    gt_path: Path = DATA_DIR / "invite_gt.json",
    policy_dir: Path = POLICY_DIR,
) -> Bundle:
    try:
        cases_source, gt_source = json_snapshot(cases_path), json_snapshot(gt_path)
        dataset = Dataset.model_validate(cases_source["snapshot"])
        gt = GroundTruthSet.model_validate(gt_source["snapshot"])
        policies = policy_snapshot(policy_dir)
        validate_bundle(
            dataset, gt, {d["snapshot"]["document_id"] for d in policies["files"]}
        )
        return Bundle(
            dataset,
            gt,
            {"dataset": cases_source, "gt": gt_source, "policies": policies},
        )
    except Exception:
        raise EvaluationError("invalid_dataset_or_gt") from None


def validate_bundle(dataset: Dataset, gt: GroundTruthSet, policy_ids: set[str]) -> None:
    if [c.case_id for c in dataset.cases] != CASE_IDS or [
        g.case_id for g in gt.cases
    ] != CASE_IDS:
        raise ValueError("Five unique ordered cases required")
    conditions = [
        ("member", 3, "active", 30),
        ("admin", 2, "active", 30),
        ("admin", 3, "expired", -30),
        ("admin", 3, "active", 30),
        ("member", 2, "expired", -30),
    ]
    universe = {"permission_denied", "member_limit_reached", "subscription_expired"}
    for case, truth, expected in zip(dataset.cases, gt.cases, conditions, strict=True):
        sub = case.subscription
        if (
            case.requester_role,
            case.member_limit,
            sub.status,
            sub.expires_offset_days,
        ) != expected:
            raise ValueError("Unexpected fixed case conditions")
        if [(u.key, u.role) for u in case.users] != [
            ("requester", case.requester_role),
            ("owner", "owner"),
        ]:
            raise ValueError("Requester included exactly once")
        if len(case.payments) != 1:
            raise ValueError("Identical successful payment required")
        causes = set()
        if case.requester_role == "member":
            causes.add("permission_denied")
        if len(case.users) >= case.member_limit:
            causes.add("member_limit_reached")
        if sub.status == "expired":
            causes.add("subscription_expired")
        if set(truth.primary_causes) != causes or len(truth.primary_causes) != len(
            causes
        ):
            raise ValueError("Ground truth cause mismatch")
        if truth.expected_outcome != ("diagnosed" if causes else "needs_information"):
            raise ValueError("Ground truth outcome mismatch")
        excluded = set(truth.excluded_causes)
        if (
            causes & excluded
            or not ((universe - causes) | {"payment_failed"}) <= excluded
        ):
            raise ValueError("Contradictory excluded causes")
        if not set(truth.policy_document_ids) <= policy_ids:
            raise ValueError("Unknown policy reference")
        if len(set(truth.required_facts)) != len(truth.required_facts):
            raise ValueError("Duplicate facts")
