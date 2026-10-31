"""Manual access-loss pilot. Live diagnosis requires --allow-live-diagnosis."""

import argparse
import json
import os
from pathlib import Path
from uuid import uuid4

from app.db import build_engine
from evaluation.access_pilot import (
    CASES,
    build_manifest,
    collect,
    execution_plan,
    insert_fixtures,
    load_ground_truth,
    next_run_name,
    save_run,
)
from evaluation.dataset import EvaluationError
from evaluation.fixtures import (
    create_run_schema,
    utc_now,
    validate_database_urls,
    verify_database,
)
from evaluation.metrics import load_pricing
from scripts.evaluate import RESULT_ROOT, diagnosis_settings


def csv(convert):
    def parse(value: str) -> list:
        return [convert(item.strip()) for item in value.split(",") if item.strip()]

    return parse


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="접근 상실 파일럿(Agent/Workflow). 채점하지 않으며 기본 외부 호출 0회."
    )
    result.add_argument("--noise", type=csv(int), default=[50, 5000])
    result.add_argument("--cases", type=csv(str), default=list(CASES))
    result.add_argument("--repetitions", type=int, default=1)
    result.add_argument("--seed", type=int, default=7)
    result.add_argument("--llm-request-timeout-seconds")
    result.add_argument("--execution-timeout-seconds")
    result.add_argument(
        "--output-dir",
        type=Path,
        help="새 run 디렉터리. 생략하면 .evaluation-results/access-<실행 수>-v<다음 버전>",
    )
    result.add_argument(
        "--allow-live-diagnosis",
        action="store_true",
        help="실제 진단 호출 및 비용 발생을 허용",
    )
    return result


def validate_args(args) -> None:
    if not args.cases or any(case_id not in CASES for case_id in args.cases):
        raise EvaluationError("invalid_cases")
    if not args.noise or any(noise < 0 for noise in args.noise):
        raise EvaluationError("invalid_noise")
    if args.repetitions < 1:
        raise EvaluationError("invalid_repetitions")


def run(args, *, create=None, settings=None) -> Path:
    validate_args(args)
    settings = settings or diagnosis_settings(
        request_timeout=args.llm_request_timeout_seconds,
        execution_timeout=args.execution_timeout_seconds,
    )
    evaluation_url = os.environ.get("EVALUATION_DATABASE_URL", "")
    database = validate_database_urls(
        evaluation_url,
        os.environ.get("DATABASE_URL", ""),
        os.environ.get("TEST_DATABASE_URL", ""),
    )
    pricing = load_pricing()
    load_ground_truth()
    run_id, reference_time = uuid4(), utc_now()
    plan = execution_plan(args.cases, args.noise, args.repetitions)
    directory = args.output_dir or RESULT_ROOT / next_run_name(RESULT_ROOT, len(plan))
    directory.mkdir(parents=True, exist_ok=False)
    manifest = build_manifest(
        run_id=run_id,
        run_name=directory.name,
        reference_time=reference_time,
        db_schema="eval_" + run_id.hex,
        seed=args.seed,
        noise_levels=args.noise,
        case_ids=args.cases,
        repetitions=args.repetitions,
        plan=plan,
        settings=settings,
        pricing=pricing,
    )
    save_run(directory, manifest, [])
    executions: list[dict] = []

    def checkpoint(done: list[dict]) -> None:
        executions[:] = done
        save_run(directory, manifest, executions)

    admin = db = None
    try:
        admin = build_engine(evaluation_url)
        verify_database(admin, database)
        db, _ = create_run_schema(admin, evaluation_url, run_id)
        insert_fixtures(db, run_id, args.cases, args.noise, args.seed, reference_time)
        collect(
            db, run_id, plan, settings, pricing, create=create, on_record=checkpoint
        )
        manifest["status"] = "collected"
    except KeyboardInterrupt:
        manifest["status"] = "interrupted"
        raise
    except EvaluationError as exc:
        manifest["status"], manifest["error_code"] = "invalid_environment", exc.code
        raise
    except Exception:
        manifest["status"] = "invalid_environment"
        manifest["error_code"] = "run_environment_error"
        raise EvaluationError("run_environment_error") from None
    finally:
        manifest["finished_at"] = utc_now().isoformat()
        save_run(directory, manifest, executions)
        if db is not None:
            db.dispose()
        if admin is not None:
            admin.dispose()
    return directory


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        # DB 생성이나 모델 호출 전에 비용 발생 승인을 확인한다.
        if not args.allow_live_diagnosis:
            raise EvaluationError("live_diagnosis_requires_allow_live_diagnosis")
        path = run(args)
    except EvaluationError as exc:
        print(json.dumps({"error": exc.code}))
        return 2
    print(json.dumps({"output_dir": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
