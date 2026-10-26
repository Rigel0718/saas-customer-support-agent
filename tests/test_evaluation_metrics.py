from copy import deepcopy
from uuid import uuid4

import pytest

from evaluation.metrics import (
    execution_metrics,
    summarize,
    trace_metrics,
    validate_pricing,
)
from evaluation.results import Execution


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
