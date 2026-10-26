"""Sequential Service E2E collector; no diagnosis retries or automatic resume."""

import time
from collections.abc import Callable
from uuid import UUID, uuid5

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.schemas.support import RequestCreate
from app.services import customer, support
from evaluation.dataset import CASE_IDS, Bundle, EvaluationError
from evaluation.fixtures import FixtureGuard, record_id, utc_now
from evaluation.results import Execution, ResultStore


def execution_plan(run_id: UUID, repetitions: int) -> list[Execution]:
    if type(repetitions) is not int or repetitions not in (1, 5):
        raise EvaluationError("invalid_repetitions")
    plan = []
    for repeat in range(1, repetitions + 1):
        modes = ("agent", "workflow") if repeat % 2 else ("workflow", "agent")
        for case_id in CASE_IDS:
            for mode in modes:
                plan.append(
                    Execution(
                        execution_id=str(uuid5(run_id, f"{repeat}:{case_id}:{mode}")),
                        case_id=case_id,
                        mode=mode,
                        repeat=repeat,
                        order=len(plan) + 1,
                    )
                )
    return plan


def traces_complete(execution: Execution) -> bool:
    request, traces = execution.request, execution.traces
    if request is None or request.status not in ("completed", "failed") or not traces:
        return False
    if [t.sequence for t in traces] != list(range(1, len(traces) + 1)):
        return False
    end = "execution_completed" if request.status == "completed" else "execution_failed"
    if traces[0].event_type != "execution_started" or traces[-1].event_type != end:
        return False
    if request.status == "completed":
        if request.diagnosis is None or not any(
            t.event_type == "final_result" and t.error_code is None for t in traces
        ):
            return False
        try:
            support.validate_diagnosis(request.diagnosis.model_dump_json(), traces)
        except Exception:
            return False
    return True


def collect_execution(
    execution: Execution,
    session_factory: Callable,
    bundle: Bundle,
    run_id: UUID,
    settings: Settings,
    *,
    create=None,
    clock=time.monotonic,
    now=utc_now,
) -> bool:
    """Return whether interrupted. Fresh recovery reads never re-run diagnosis."""
    returned = None
    interrupted = False
    context = None
    try:
        with session_factory() as session:
            context = customer.authenticate(
                session, record_id(run_id, execution.case_id, "requester")
            )
            before = clock()
            try:
                request = support.create_request(
                    session, context, RequestCreate(content=bundle.dataset.content)
                )
                execution.request_id = str(request.id)
                execution.request = request
                execution.started = True
                returned = support.diagnose_request(
                    session,
                    context,
                    request.id,
                    settings,
                    create=create,
                    now=now,
                    mode=execution.mode,
                )
            except KeyboardInterrupt:
                interrupted = True
                execution.errors.append({"domain": "execution", "code": "interrupted"})
            except Exception as exc:
                domain = "execution" if execution.started else "preparation"
                code = (
                    "database_error"
                    if isinstance(exc, SQLAlchemyError)
                    else "service_error"
                )
                execution.errors.append({"domain": domain, "code": code})
            finally:
                execution.elapsed_ms = max(0.0, (clock() - before) * 1000)
    except Exception:
        execution.errors.append(
            {"domain": "preparation", "code": "authentication_or_session_failed"}
        )
    if returned is not None:
        execution.request = returned
    if execution.request_id and context is not None:
        # A fresh transaction can observe a commit whose response was lost.
        try:
            with session_factory() as recovery:
                request = support.get_request(
                    recovery, context, UUID(execution.request_id)
                )
                execution.request = support.request_dto(recovery, request)
        except Exception:
            execution.errors.append(
                {"domain": "collection", "code": "request_collection_failed"}
            )
        try:
            with session_factory() as recovery:
                execution.traces = support.get_traces(
                    recovery, context, UUID(execution.request_id)
                ).items
        except Exception:
            execution.errors.append(
                {"domain": "collection", "code": "trace_collection_failed"}
            )
    if not execution.started:
        execution.state = "environment_error"
        execution.execution_success = None
    else:
        request = execution.request
        if request and request.status == "completed" and request.diagnosis is not None:
            execution.state, execution.execution_success = "completed", True
        elif request and request.status == "failed":
            execution.state, execution.execution_success = "failed", False
        else:
            execution.state, execution.execution_success, execution.censored = (
                "incomplete",
                False,
                True,
            )
    execution.trace_complete = traces_complete(execution)
    return interrupted


def run_collection(
    store: ResultStore,
    guard: FixtureGuard,
    settings: Settings,
    *,
    create=None,
    session_factory=None,
    clock=time.monotonic,
    now=utc_now,
) -> None:
    from evaluation.metrics import execution_metrics, summarize

    if store.manifest.status != "prepared" or any(
        e.state != "not_started" for e in store.executions
    ):
        raise EvaluationError("collection_resume_not_supported")
    session_factory = session_factory or (lambda: Session(guard.engine))
    store.manifest.status = "collecting"
    try:
        store.save_collection()
        for execution in store.executions:
            guard.check(now=now)
            interrupted = collect_execution(
                execution,
                session_factory,
                guard.bundle,
                guard.run_id,
                settings,
                create=create,
                clock=clock,
                now=now,
            )
            execution.metrics = execution_metrics(execution, store.manifest.pricing)
            store.manifest.completed_count = sum(
                e.state != "not_started" for e in store.executions
            )
            store.manifest.response_models = sorted(
                {
                    t.model
                    for e in store.executions
                    for t in e.traces
                    if t.event_type == "llm_response" and t.model
                }
            )
            if len(store.manifest.response_models) > 1:
                limitation = "Multiple actual response models observed; alias version differences limit comparison."
                if limitation not in store.manifest.comparison_limitations:
                    store.manifest.comparison_limitations.append(limitation)
            store.save_collection()
            if interrupted:
                store.manifest.status = "interrupted"
                break
            if not execution.started:
                raise EvaluationError("pre_execution_environment_error")
            guard.check(now=now)
        else:
            store.manifest.status = "collected"
    except KeyboardInterrupt:
        store.manifest.status, store.manifest.error_code = "interrupted", "interrupted"
    except EvaluationError as exc:
        store.manifest.status = (
            "storage_failed"
            if exc.code == "result_storage_failed"
            else "invalid_environment"
        )
        store.manifest.error_code = exc.code
    finally:
        store.manifest.finished_at = now().isoformat()
        # If the disk is unavailable, the previous atomic files remain readable.
        store.save_collection()
        store.summary = summarize(
            store.executions, [], run_status=store.manifest.status
        )
        store.save_grading()
