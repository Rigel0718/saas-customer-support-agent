"""Knowledge answer experiment. Live model calls require --allow-live-diagnosis."""

import argparse
import json
import os
from dataclasses import replace
from pathlib import Path

from sqlalchemy.orm import Session

from app.db import build_engine
from evaluation.dataset import EvaluationError
from evaluation.fixtures import utc_now
from evaluation.knowledge_answer import (
    CASES_PATH,
    DERIVED_BUDGET,
    SOURCE,
    build_manifest,
    case_inputs,
    collect,
    execution_plan,
    load_cases,
    next_run_name,
    save_run,
)
from evaluation.metrics import load_pricing
from scripts.evaluate import RESULT_ROOT, diagnosis_settings


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="지식 답변 실험(Agent/Workflow). 채점하지 않으며 기본 외부 호출 0회."
    )
    result.add_argument("--cases-path", type=Path, default=CASES_PATH)
    result.add_argument("--source", default=SOURCE)
    result.add_argument(
        "--cases",
        type=lambda value: [x.strip() for x in value.split(",") if x.strip()],
        help="쉼표로 구분한 case_id. 생략하면 전체",
    )
    result.add_argument("--repetitions", type=int, default=1)
    result.add_argument("--execution-timeout-seconds", default="180")
    result.add_argument("--llm-request-timeout-seconds", default="60")
    result.add_argument("--max-steps", type=int, default=16)
    result.add_argument("--max-tool-calls", type=int, default=24)
    result.add_argument(
        "--output-dir",
        type=Path,
        help="새 run 디렉터리. 생략하면 .evaluation-results/knowledge-<실행 수>-v<다음 버전>",
    )
    result.add_argument(
        "--allow-live-diagnosis",
        action="store_true",
        help="실제 모델 호출 및 비용 발생을 허용",
    )
    return result


def experiment_settings(args):
    settings = diagnosis_settings(
        request_timeout=args.llm_request_timeout_seconds,
        execution_timeout=args.execution_timeout_seconds,
    )
    try:
        settings = replace(
            settings,
            database_url=os.environ.get("DATABASE_URL", ""),
            agent_max_steps=args.max_steps,
            agent_max_tool_calls=args.max_tool_calls,
        )
    except ValueError:
        raise EvaluationError("invalid_agent_limits") from None
    # 사후 환산은 Trace를 자르는 방식이라 실행 예산이 환산 예산 이상이어야 한다.
    if (
        settings.agent_timeout_seconds < DERIVED_BUDGET["execution_timeout_seconds"]
        or settings.agent_max_steps < DERIVED_BUDGET["agent_max_steps"]
        or settings.agent_max_tool_calls < DERIVED_BUDGET["agent_max_tool_calls"]
        or settings.llm_request_timeout_seconds
        < DERIVED_BUDGET["llm_request_timeout_seconds"]
    ):
        raise EvaluationError("run_budget_below_derived_budget")
    return settings


def run(args, *, create=None, settings=None, session=None) -> Path:
    if args.repetitions < 1:
        raise EvaluationError("invalid_repetitions")
    settings = settings or experiment_settings(args)
    pricing = load_pricing()
    cases = load_cases(args.cases_path)
    case_ids = args.cases or [case.case_id for case in cases.cases]
    engine = None
    if session is None:
        engine = build_engine(settings.database_url)
        session = Session(engine)
    try:
        inputs = case_inputs(session, cases, case_ids, args.source)
        plan = execution_plan(case_ids, args.repetitions)
        directory = args.output_dir or RESULT_ROOT / next_run_name(
            RESULT_ROOT, len(plan)
        )
        directory.mkdir(parents=True, exist_ok=False)
        manifest = build_manifest(
            run_name=directory.name,
            started_at=utc_now(),
            source=args.source,
            cases_path=args.cases_path,
            snapshot_id=cases.snapshot_id,
            case_ids=case_ids,
            repetitions=args.repetitions,
            plan=plan,
            settings=settings,
            pricing=pricing,
        )
        executions: list[dict] = []
        save_run(directory, manifest, executions)

        def checkpoint(done: list[dict]) -> None:
            executions[:] = done
            save_run(directory, manifest, executions)

        try:
            collect(
                session,
                inputs,
                plan,
                settings,
                pricing,
                create=create,
                on_record=checkpoint,
            )
            manifest["status"] = "collected"
        except KeyboardInterrupt:
            manifest["status"] = "interrupted"
            raise
        except Exception:
            manifest["status"] = "invalid_environment"
            manifest["error_code"] = "run_environment_error"
            raise EvaluationError("run_environment_error") from None
        finally:
            manifest["finished_at"] = utc_now().isoformat()
            save_run(directory, manifest, executions)
    finally:
        if engine is not None:
            session.close()
            engine.dispose()
    return directory


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        # DB 연결이나 모델 호출 전에 비용 발생 승인을 확인한다.
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
