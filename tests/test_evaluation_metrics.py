from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from evaluation.metrics import (
    execution_metrics,
    summarize,
    trace_metrics,
    validate_pricing,
)
from evaluation.results import Execution
from app.schemas.support import FailureMetadata, TraceDTO
from tests.test_support_models import NOW


@pytest.fixture
def pricing():
    return {
        "pricing_version": "synthetic-test-v1",
        "as_of": "2026-01-01",
        "currency": "USD",
        "source": "synthetic unit-test rates",
        "assumptions": "Synthetic, not actual prices",
        "models": {
            "response-model": {
                "input_usd_per_million": "2",
                "output_usd_per_million": "4",
            }
        },
    }


def llm(usage=None, model="response-model", **extra):
    return {"event_type": "llm_response", "usage": usage, "model": model, **extra}


def test_errors_count_and_fieldwise_partial_usage(pricing):
    traces = [
        llm({"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}),
        llm({"input_tokens": 20, "output_tokens": None}, error_code="llm_error"),
        {"event_type": "tool_result", "error_code": "not_found"},
    ]
    m = trace_metrics(traces, True, pricing)
    assert m["llm_calls"]["value"] == 2 and m["tool_calls"]["value"] == 1
    assert m["input_tokens"]["value"] == 120
    assert m["output_tokens"]["value"] is None and m["output_tokens"]["observed"] == 50
    assert m["total_tokens"]["observed"] == 150
    assert m["estimated_cost_usd"]["observed"] == "0.000440000000"
    assert m["estimated_cost_usd"]["value"] is None


def test_zero_is_only_zero_with_complete_trace(pricing):
    for complete in (True, False):
        m = trace_metrics([], complete, pricing)
        assert m["llm_calls"]["value"] == (0 if complete else None)
        assert m["estimated_cost_usd"]["value"] == ("0E-12" if complete else None)
    m = trace_metrics([llm()], True, pricing)
    assert m["input_tokens"]["value"] is None


def test_decimal_per_response_model_and_unknown_price(pricing):
    traces = [llm({"input_tokens": 100, "output_tokens": 50, "total_tokens": 150})] * 2
    assert (
        trace_metrics(traces, True, pricing)["estimated_cost_usd"]["value"]
        == "0.000800000000"
    )
    traces.append(llm({"input_tokens": 1, "output_tokens": 2}, model="unknown"))
    cost = trace_metrics(traces, True, pricing)["estimated_cost_usd"]
    assert cost["value"] is None and cost["observed"] == "0.000800000000"
    assert "unknown_model_or_price" in cost["reasons"]
    validate_pricing(pricing)
    broken = deepcopy(pricing)
    broken["models"]["response-model"]["input_usd_per_million"] = "NaN"
    with pytest.raises(Exception, match="invalid_pricing"):
        validate_pricing(broken)


def test_denominators_failed_pending_environment_and_censoring(pricing):
    executions = []
    for state, success, started, elapsed, censored in [
        ("completed", True, True, 10, False),
        ("failed", False, True, 30, False),
        ("completed", True, True, 20, False),
        ("environment_error", None, False, 5, False),
        ("incomplete", False, True, 50, True),
    ]:
        e = Execution(
            execution_id=str(uuid4()),
            case_id="C01",
            mode="agent",
            repeat=1,
            order=len(executions) + 1,
            state=state,
            started=started,
            execution_success=success,
            elapsed_ms=float(elapsed),
            censored=censored,
        )
        e.trace_complete = state != "incomplete"
        e.metrics = execution_metrics(e, pricing)
        executions.append(e)
    judgments = [
        {"execution_id": executions[0].execution_id, "final_verdict": "correct"},
        {"execution_id": executions[2].execution_id, "final_verdict": "pending"},
    ]
    group = summarize(executions, judgments)["by_mode"]["agent"]
    assert group["execution_success"] == {
        "numerator": 2,
        "denominator": 4,
        "value": 0.5,
    }
    assert group["correctness"]["denominator"] == 1
    latency = group["all_started"]["latency_ms"]
    assert (
        latency["count"],
        latency["mean"],
        latency["median"],
        latency["min"],
        latency["max"],
    ) == (3, 20, 20, 10, 30)
    assert latency["censored_count"] == latency["missing_count"] == 1
    assert group["all_started"]["input_tokens"]["sum"] is None
    assert group["all_started"]["input_tokens"]["observed_sum"] == 0
    assert group["successful_metrics"]["latency_ms"]["mean"] == 15
    assert summarize([], [])["by_mode"]["agent"]["correctness"]["value"] is None


def test_review_required_remains_separate_from_correctness_denominator():
    executions, judgments = [], []
    for order, verdict in enumerate(("correct", "incorrect", "review_required"), 1):
        execution = Execution(
            execution_id=str(uuid4()),
            case_id="C01",
            mode="agent",
            repeat=1,
            order=order,
            state="completed",
            started=True,
            execution_success=True,
        )
        executions.append(execution)
        judgments.append(
            {"execution_id": execution.execution_id, "final_verdict": verdict}
        )
    summary = summarize(executions, judgments)
    for group in (summary["by_mode"]["agent"], summary["by_case_mode"]["C01:agent"]):
        assert group["verdict_counts"]["review_required"] == 1
        assert group["correctness"] == {"numerator": 1, "denominator": 2, "value": 0.5}
        assert group["execution_success"]["value"] == 1


def output_failure(mode="agent", *, trace_count=1, metadata=True):
    detail = FailureMetadata(
        failure_stage="diagnosis_schema", failure_reason="validation_error"
    ) if metadata else None
    traces = [
        TraceDTO(
            sequence=i + 1, event_type="final_result", created_at=NOW,
            error_code="invalid_output", failure_metadata=detail,
        )
        for i in range(trace_count)
    ]
    # Multiple terminal events must never count as additional executions.
    traces.extend(
        TraceDTO(
            sequence=len(traces) + i + 1, event_type="execution_failed",
            created_at=NOW, error_code="invalid_output", failure_metadata=detail,
        ) for i in range(2)
    )
    return SimpleNamespace(
        execution_id=str(uuid4()), case_id="C03", mode=mode,
        state="failed", started=True, execution_success=False,
        request=SimpleNamespace(status="failed", error_code="invalid_output", diagnosis=None),
        traces=traces, trace_complete=False, censored=False, metrics={},
    )


@pytest.mark.parametrize("mode", ["agent", "workflow"])
def test_output_failure_count_is_per_execution_and_legacy_is_unclassified(mode):
    classified = output_failure(mode)
    legacy = output_failure(mode, metadata=False)
    incomplete = output_failure(mode)
    incomplete.state = "incomplete"
    other_error = output_failure(mode)
    other_error.request.error_code = "database_error"
    success = output_failure(mode)
    success.state = success.request.status = "completed"
    success.execution_success = True
    not_started = output_failure(mode)
    not_started.started = False
    not_started.state = "environment_error"
    items = [classified, legacy, incomplete, other_error, success, not_started]
    summary = summarize(items, [])
    for group in (summary["by_mode"][mode], summary["by_case_mode"][f"C03:{mode}"]):
        assert group["invalid_output"] == {
            "count": 2,
            "rate": {"numerator": 2, "denominator": 5, "value": 0.4},
            "classified_count": 1,
            "unclassified_count": 1,
            "by_reason": [{
                "failure_stage": "diagnosis_schema",
                "failure_reason": "validation_error", "count": 1,
            }],
        }
        assert group["execution_success"] == {"numerator": 1, "denominator": 5, "value": 0.2}


@pytest.mark.parametrize("finals,metadata,classified", [(0, True, 0), (2, True, 0), (1, False, 0), (1, True, 1)])
def test_output_failure_ambiguous_or_partial_traces(finals, metadata, classified):
    execution = output_failure(trace_count=finals, metadata=metadata)
    detail = summarize([execution], [])["by_mode"]["agent"]["invalid_output"]
    assert detail["count"] == 1
    assert detail["classified_count"] == classified
    assert detail["unclassified_count"] == 1 - classified
    assert sum(r["count"] for r in detail["by_reason"]) == classified


def test_output_failure_ordering_and_zero_denominator():
    schema = output_failure()
    evidence_failure = output_failure()
    evidence_failure.traces[0].failure_metadata = FailureMetadata(
        failure_stage="evidence_reference", failure_reason="invalid_trace_reference"
    )
    detail = summarize([evidence_failure, schema, output_failure()], [])["by_mode"]["agent"]["invalid_output"]
    assert [r["failure_stage"] for r in detail["by_reason"]] == ["diagnosis_schema", "evidence_reference"]
    assert [r["count"] for r in detail["by_reason"]] == [2, 1]
    empty = summarize([], [])["by_mode"]["agent"]["invalid_output"]
    assert empty == {
        "count": 0, "rate": {"numerator": 0, "denominator": 0, "value": None},
        "classified_count": 0, "unclassified_count": 0, "by_reason": [],
    }
    missing_result = output_failure()
    missing_result.request = None
    missing_result.state = "incomplete"
    zero = summarize([missing_result], [])["by_mode"]["agent"]["invalid_output"]
    assert zero["rate"] == {"numerator": 0, "denominator": 1, "value": 0}
    assert zero["by_reason"] == []


def test_mixed_final_results_are_not_a_unique_failure_source():
    execution = output_failure()
    execution.traces.append(TraceDTO(sequence=4, event_type="final_result", created_at=NOW))
    detail = summarize([execution], [])["by_mode"]["agent"]["invalid_output"]
    assert detail["classified_count"] == 0
    assert detail["unclassified_count"] == 1
