"""Manual evaluation CLI. Live diagnosis and judge each require a separate flag."""

import argparse
import json
import os
from pathlib import Path
from uuid import uuid4

from app.config import Settings, timeout_settings
from app.db import build_engine
from evaluation.dataset import DATA_DIR, EvaluationError, load_bundle
from evaluation.fixtures import (
    FixtureGuard,
    create_run_schema,
    insert_customers,
    utc_now,
    validate_database_urls,
    verify_database,
)
from evaluation.judge import (
    Judge,
    JudgeConfig,
    grade_store,
    load_calibration,
    run_calibration,
)
from evaluation.metrics import load_pricing, summarize
from evaluation.results import ResultStore, atomic_json, build_manifest, prepare_regrade
from evaluation.runner import execution_plan, run_collection

RESULT_ROOT = Path(".evaluation-results")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Agent/Workflow 초대 실패 평가. 기본 외부 호출 0회."
    )
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("plan", "run", "calibrate", "judge"):
        command = commands.add_parser(name)
        command.add_argument(
            "--dataset", type=Path, default=DATA_DIR / "invite_cases.json"
        )
        command.add_argument("--gt", type=Path, default=DATA_DIR / "invite_gt.json")
        command.add_argument("--pricing", type=Path, default=DATA_DIR / "pricing.json")
        if name in ("plan", "run"):
            command.add_argument("--repetitions", type=int, choices=(1, 5), default=1)
        if name == "run":
            command.add_argument(
                "--llm-request-timeout-seconds",
                help="두 모드 공통 LLM 요청 Timeout(초), 환경변수보다 우선",
            )
            command.add_argument(
                "--execution-timeout-seconds",
                help="두 모드 공통 실행 Budget(초), 모드별 환경변수보다 우선",
            )
            command.add_argument(
                "--allow-live-diagnosis",
                action="store_true",
                help="실제 진단 호출 및 비용 발생을 허용",
            )
            command.add_argument(
                "--output-dir", type=Path, help="새 run 디렉터리. 기존 경로는 거부"
            )
        if name in ("calibrate", "judge"):
            command.add_argument(
                "--allow-live-judge",
                action="store_true",
                help="실제 Judge 호출 및 비용 발생을 허용",
            )
        if name == "calibrate":
            command.add_argument(
                "--calibration-file",
                type=Path,
                default=DATA_DIR / "judge_calibration.json",
            )
            command.add_argument(
                "--output", type=Path, help="새 Calibration 결과 JSON. 기존 파일은 거부"
            )
        if name == "judge":
            command.add_argument("--run-dir", type=Path, required=True)
            command.add_argument(
                "--calibration-result",
                type=Path,
                help="사전 사람 검토 후 생성한 Calibration 결과 JSON",
            )
            command.add_argument(
                "--regrade",
                action="store_true",
                help="원본을 보존하고 regrades/<id>에 새 네 JSON 저장",
            )
    return result


def diagnosis_settings(
    *,
    request_timeout: str | None = None,
    execution_timeout: str | None = None,
) -> Settings:
    try:
        timeouts = timeout_settings(
            request_timeout=request_timeout, execution_timeout=execution_timeout
        )
    except ValueError:
        raise EvaluationError("invalid_timeout_configuration") from None
    if timeouts["agent_timeout_seconds"] != timeouts["workflow_timeout_seconds"]:
        raise EvaluationError(
            "unequal_execution_budgets_use_--execution-timeout-seconds"
        )
    model, key = os.environ.get("OPENAI_MODEL"), os.environ.get("OPENAI_API_KEY")
    if not model or not key:
        raise EvaluationError("missing_diagnosis_configuration")
    # Standard step/tool limits and equal timeouts; no bearer config dependency.
    return Settings(
        os.environ.get("EVALUATION_DATABASE_URL", ""),
        {},
        openai_api_key=key,
        openai_model=model,
        **timeouts,
    )


def judge_client(bundle, pricing) -> Judge:
    model, key = (
        os.environ.get("EVALUATION_JUDGE_MODEL"),
        os.environ.get("OPENAI_API_KEY"),
    )
    if not model or not key:
        raise EvaluationError("missing_judge_configuration")
    return Judge(JudgeConfig(model, api_key=key), bundle, pricing)


def run_command(args, bundle, pricing) -> Path:
    settings = diagnosis_settings(
        request_timeout=args.llm_request_timeout_seconds,
        execution_timeout=args.execution_timeout_seconds,
    )
    evaluation_url = os.environ.get("EVALUATION_DATABASE_URL", "")
    database = validate_database_urls(
        evaluation_url,
        os.environ.get("DATABASE_URL", ""),
        os.environ.get("TEST_DATABASE_URL", ""),
    )
    run_id, reference_time = uuid4(), utc_now()
    plan = execution_plan(run_id, args.repetitions)
    store = ResultStore(
        args.output_dir or RESULT_ROOT / str(run_id),
        build_manifest(
            run_id, reference_time, args.repetitions, plan, bundle, settings, pricing
        ),
        plan,
    )
    store.initialize()
    admin = db = None
    try:
        admin = build_engine(evaluation_url)
        verify_database(admin, database)
        db, schema = create_run_schema(admin, evaluation_url, run_id)
        store.manifest.db_schema = schema
        insert_customers(db, bundle, run_id, reference_time)
        guard = FixtureGuard.capture(db, bundle, run_id, reference_time)
        run_collection(store, guard, settings)
    except EvaluationError as exc:
        store.manifest.status, store.manifest.error_code = (
            "invalid_environment",
            exc.code,
        )
        store.manifest.finished_at = utc_now().isoformat()
        store.save_collection()
        store.summary = summarize(
            store.executions, [], run_status=store.manifest.status
        )
        store.save_grading()
        raise
    except Exception:
        store.manifest.status, store.manifest.error_code = (
            "invalid_environment",
            "run_environment_error",
        )
        store.manifest.finished_at = utc_now().isoformat()
        store.save_collection()
        store.summary = summarize(
            store.executions, [], run_status=store.manifest.status
        )
        store.save_grading()
        raise EvaluationError("run_environment_error") from None
    finally:
        if db is not None:
            db.dispose()
        if admin is not None:
            admin.dispose()
    if store.manifest.status != "collected":
        raise EvaluationError(store.manifest.error_code or store.manifest.status)
    return store.directory


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        # Gate before environment loading, DB creation, SDK construction, or model calls.
        if args.command == "run" and not args.allow_live_diagnosis:
            raise EvaluationError("live_diagnosis_requires_allow_live_diagnosis")
        if args.command in ("calibrate", "judge") and not args.allow_live_judge:
            raise EvaluationError("live_judge_requires_allow_live_judge")
        bundle = load_bundle(args.dataset, args.gt)
        pricing = load_pricing(args.pricing)
        if args.command == "plan":
            run_id = uuid4()
            plan = execution_plan(run_id, args.repetitions)
            print(
                json.dumps(
                    {
                        "repetitions": args.repetitions,
                        "execution_count": len(plan),
                        "dataset_sha256": bundle.provenance["dataset"]["sha256"],
                        "gt_sha256": bundle.provenance["gt"]["sha256"],
                        "plan": [
                            e.model_dump(include={"repeat", "case_id", "mode", "order"})
                            for e in plan
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "run":
            path = run_command(args, bundle, pricing)
        elif args.command == "calibrate":
            calibration = load_calibration(args.calibration_file)
            path = args.output or RESULT_ROOT / "calibrations" / f"{uuid4()}.json"
            # Reserve before calls; never overwrite a previous calibration report.
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as file:
                json.dump(
                    {"status": "incomplete", "schema_version": "calibration-v1"}, file
                )
            result = run_calibration(judge_client(bundle, pricing), calibration)
            atomic_json(path, result)
        else:
            source = ResultStore.load(args.run_dir)
            if (
                source.manifest.provenance["dataset"]["sha256"]
                != bundle.provenance["dataset"]["sha256"]
            ):
                raise EvaluationError("judge_dataset_mismatch")
            calibration = (
                json.loads(args.calibration_result.read_bytes())
                if args.calibration_result
                else None
            )
            judge = judge_client(bundle, pricing)
            target = (
                prepare_regrade(source, bundle, pricing) if args.regrade else source
            )
            grade_store(target, judge, calibration)
            path = target.directory
        print(str(path))
        return 0
    except EvaluationError as exc:
        print(f"평가 중단: {exc.code}")
        return 1
    except KeyboardInterrupt:
        print("평가 중단: interrupted")
        return 130
    except Exception:
        # Do not print exceptions from SDK/DB/configuration; they may contain secrets.
        print("평가 중단: evaluation_io_or_configuration_error")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
