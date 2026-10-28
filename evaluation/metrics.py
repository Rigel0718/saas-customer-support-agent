"""Seven metrics, with missing observations kept distinct from measured zero."""

import json
from collections import Counter
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path
from statistics import median

from evaluation.dataset import DATA_DIR, EvaluationError

COST_QUANTUM = Decimal("0.000000000001")
TOKEN_FIELDS = ("input_tokens", "output_tokens", "total_tokens")
NUMERIC_METRICS = (
    "latency_ms",
    *TOKEN_FIELDS,
    "llm_calls",
    "tool_calls",
    "estimated_cost_usd",
)


def usd(value: Decimal) -> str:
    return str(value.quantize(COST_QUANTUM, rounding=ROUND_HALF_EVEN))


def validate_pricing(data: dict) -> dict:
    try:
        if set(data) != {
            "pricing_version",
            "as_of",
            "currency",
            "source",
            "assumptions",
            "models",
        }:
            raise ValueError
        if (
            not data["pricing_version"]
            or data["currency"] != "USD"
            or not isinstance(data["models"], dict)
        ):
            raise ValueError
        if data["models"]:
            date.fromisoformat(data["as_of"])
            if (
                not isinstance(data["source"], str)
                or not data["source"]
                or not data["assumptions"]
            ):
                raise ValueError
        for model, rates in data["models"].items():
            if (
                not isinstance(model, str)
                or not model
                or set(rates) != {"input_usd_per_million", "output_usd_per_million"}
            ):
                raise ValueError
            for rate in rates.values():
                # Decimal strings avoid binary rounding of money in JSON.
                if (
                    not isinstance(rate, str)
                    or not Decimal(rate).is_finite()
                    or Decimal(rate) < 0
                ):
                    raise ValueError
        return data
    except (ValueError, TypeError, KeyError, InvalidOperation):
        raise EvaluationError("invalid_pricing") from None


def load_pricing(path: Path = DATA_DIR / "pricing.json") -> dict:
    try:
        return validate_pricing(json.loads(path.read_bytes()))
    except Exception:
        raise EvaluationError("invalid_pricing") from None


def measure(observed, complete: bool, *, missing_count=0, reasons=()) -> dict:
    return {
        "value": observed if complete else None,
        "observed": observed,
        "complete": complete,
        "missing_count": missing_count,
        "reasons": list(reasons),
    }


def valid_tokens(value) -> bool:
    return type(value) is int and value >= 0


def trace_metrics(traces: list[dict], complete: bool, pricing: dict) -> dict:
    llms = [t for t in traces if t["event_type"] == "llm_response"]
    tools = [t for t in traces if t["event_type"] == "tool_result"]
    reasons = [] if complete else ["incomplete_trace"]
    result = {
        "llm_calls": measure(len(llms), complete, reasons=reasons),
        "tool_calls": measure(len(tools), complete, reasons=reasons),
    }
    for field in TOKEN_FIELDS:
        values = [(t.get("usage") or {}).get(field) for t in llms]
        missing = sum(not valid_tokens(v) for v in values)
        result[field] = measure(
            sum(v for v in values if valid_tokens(v)),
            complete and missing == 0,
            missing_count=missing,
            reasons=reasons + (["missing_usage"] if missing else []),
        )
    observed_cost = Decimal(0)
    missing_calls = 0
    cost_reasons = set(reasons)
    for call in llms:
        rates = pricing["models"].get(call.get("model"))
        usage = call.get("usage") or {}
        if rates is None:
            missing_calls += 1
            cost_reasons.add("unknown_model_or_price")
            continue
        missing = False
        for field, rate_key in (
            ("input_tokens", "input_usd_per_million"),
            ("output_tokens", "output_usd_per_million"),
        ):
            value = usage.get(field)
            if not valid_tokens(value):
                missing = True
                cost_reasons.add("missing_usage")
            else:
                observed_cost += (
                    Decimal(value) * Decimal(rates[rate_key]) / Decimal(1_000_000)
                )
        missing_calls += int(missing)
    result["estimated_cost_usd"] = measure(
        usd(observed_cost),
        complete and missing_calls == 0,
        missing_count=missing_calls,
        reasons=sorted(cost_reasons),
    )
    return result


def execution_metrics(execution, pricing: dict) -> dict:
    result = trace_metrics(
        [t.model_dump(mode="json") for t in execution.traces],
        execution.trace_complete,
        pricing,
    )
    result["latency_ms"] = measure(
        execution.elapsed_ms,
        execution.started
        and not execution.censored
        and execution.elapsed_ms is not None,
        reasons=["censored"] if execution.censored else [],
    )
    return result


def numeric_statistics(executions: list, metric: str) -> dict:
    measures = [e.metrics.get(metric, {}) for e in executions]
    values = [m["value"] for m in measures if m.get("value") is not None]
    is_cost = metric == "estimated_cost_usd"
    convert = Decimal if is_cost else lambda v: v
    numbers = [convert(v) for v in values]
    observed = sum(
        (convert(m["observed"]) for m in measures if m.get("observed") is not None),
        Decimal(0) if is_cost else 0,
    )
    serialize = usd if is_cost else lambda v: v
    count = len(numbers)
    missing = len(executions) - count
    return {
        "count": count,
        "missing_count": missing,
        "censored_count": sum(e.censored for e in executions)
        if metric == "latency_ms"
        else 0,
        "mean": serialize(sum(numbers) / count) if count else None,
        "median": serialize(median(numbers)) if count else None,
        "min": serialize(min(numbers)) if count else None,
        "max": serialize(max(numbers)) if count else None,
        "sum": serialize(sum(numbers)) if count and not missing else None,
        "observed_sum": serialize(observed),
    }


def ratio(numerator: int, denominator: int) -> dict:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def invalid_output_summary(started: list) -> dict:
    failures = [
        execution for execution in started
        if execution.state == "failed"
        and execution.request is not None
        and execution.request.status == "failed"
        and execution.request.error_code == "invalid_output"
    ]
    counts = Counter()
    for execution in failures:
        finals = [
            trace for trace in execution.traces
            if trace.event_type == "final_result"
        ]
        if (
            len(finals) == 1
            and finals[0].error_code == "invalid_output"
            and finals[0].failure_metadata is not None
        ):
            metadata = finals[0].failure_metadata
            counts[(metadata.failure_stage, metadata.failure_reason)] += 1
    classified = sum(counts.values())
    return {
        "count": len(failures),
        "rate": ratio(len(failures), len(started)),
        "classified_count": classified,
        "unclassified_count": len(failures) - classified,
        "by_reason": [
            {"failure_stage": stage, "failure_reason": reason, "count": count}
            for (stage, reason), count in sorted(counts.items())
        ],
    }


def summarize(
    executions: list, judgments: list[dict], *, run_status="collected", calibration=None
) -> dict:
    by_id = {j["execution_id"]: j for j in judgments}

    def group(items):
        started = [e for e in items if e.started]
        successful = [e for e in started if e.execution_success]
        verdicts = Counter(
            by_id.get(e.execution_id, {}).get(
                "final_verdict",
                "pending" if e.request and e.request.diagnosis else "not_applicable",
            )
            for e in items
        )
        return {
            "planned": len(items),
            "started": len(started),
            "not_started": sum(e.state == "not_started" for e in items),
            "successful": len(successful),
            "failed": sum(e.state == "failed" for e in items),
            "incomplete": sum(e.state == "incomplete" for e in items),
            "environment_errors": sum(e.state == "environment_error" for e in items),
            "verdict_counts": {
                key: verdicts[key]
                for key in (
                    "correct",
                    "incorrect",
                    "review_required",
                    "pending",
                    "not_applicable",
                )
            },
            "correctness": ratio(
                verdicts["correct"], verdicts["correct"] + verdicts["incorrect"]
            ),
            "execution_success": ratio(len(successful), len(started)),
            "invalid_output": invalid_output_summary(started),
            "all_started": {
                metric: numeric_statistics(started, metric)
                for metric in NUMERIC_METRICS
            },
            "successful_metrics": {
                metric: numeric_statistics(successful, metric)
                for metric in NUMERIC_METRICS
            },
        }

    return {
        "run_status": run_status,
        "valid_comparison": run_status == "collected",
        "calibration": calibration or {"status": "uncalibrated"},
        "quality_conclusion_ready": run_status == "collected"
        and (calibration or {}).get("status") == "calibrated",
        "by_mode": {
            mode: group([e for e in executions if e.mode == mode])
            for mode in ("agent", "workflow")
        },
        "by_case_mode": {
            f"{case}:{mode}": group(
                [e for e in executions if e.case_id == case and e.mode == mode]
            )
            for case in sorted({e.case_id for e in executions})
            for mode in ("agent", "workflow")
        },
        "limitations": [
            "Small fixed sample; no universal winner, statistical significance, or isolated causal effect.",
            "Cost is uncached list-rate USD estimation, not billed cost; judge overhead is separate.",
            "Calibrated applies only to the reviewed set and matching versions, not general judge accuracy.",
        ],
    }
