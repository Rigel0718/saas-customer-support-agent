import json
from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from app.schemas.support import DiagnosisDTO
from evaluation.dataset import load_bundle
from evaluation.judge import (
    Judge,
    JudgeConfig,
    applicable_calibration,
    load_calibration,
    run_calibration,
)
from evaluation.metrics import load_pricing
from tests.test_agent_loop import response


def semantic(label="correct", **changes):
    result = dict(
        semantic_verdict=label,
        reason="필수 원인을 동의 표현으로 식별했다.",
        missing_causes=[],
        missing_facts=[],
        incorrect_causes=[],
    )
    return result | changes


def judge_response(label="correct", **changes):
    return response(
        [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            semantic(label, **changes), ensure_ascii=False
                        ),
                    }
                ],
            }
        ],
        usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )


def diagnosis(outcome="diagnosed", **changes):
    return DiagnosisDTO.model_validate(
        dict(
            outcome=outcome,
            summary="일반 구성원은 초대할 수 없습니다.",
            causes=["일반 구성원 권한 부재"],
            next_actions=["추가 확인"],
            evidence=[
                {
                    "trace_sequence": 3,
                    "policy_document_id": "permissions",
                    "statement": "관찰 사실",
                }
            ],
        )
        | changes
    )


def reviewed_calibration():
    calibration = load_calibration().model_copy(deep=True)
    calibration.examples_reviewed = True
    calibration.examples_reviewer = "synthetic-test-reviewer"
    calibration.examples_review_version = "synthetic-review-v1"
    labels = (
        ["correct"] * 5
        + ["incorrect"] * 4
        + [
            "review_required",
            "correct",
            "review_required",
            "incorrect",
            "incorrect",
            "correct",
        ]
    )
    for example, label in zip(calibration.examples, labels, strict=True):
        example.human.label = label
        example.human.reason = "Synthetic unit test only, not human review"
        example.human.reviewer = "synthetic-test-reviewer"
        example.human.review_version = "synthetic-review-v1"
    return calibration


def test_blind_allowlist_independent_requests_and_finite_timeout():
    calls = []

    def create(**kw):
        calls.append(kw)
        return judge_response()

    judge = Judge(
        JudgeConfig("judge-alias"), load_bundle(), load_pricing(), create=create
    )
    for mode in ("agent-id-hidden", "workflow-id-hidden"):
        result = judge.grade(mode, "C01", diagnosis())
        assert result["final_verdict"] == "correct"
        assert result["versions"]["gt_sha256"]
        assert result["overhead"]["llm_calls"]["value"] == 1
        assert result["overhead"]["input_tokens"]["value"] == 10
    assert len(calls) == 2
    payload = json.loads(calls[0]["input"][0]["content"])
    assert set(payload) == {"ground_truth", "diagnosis"}
    assert set(payload["diagnosis"]) == {"summary", "causes"}
    assert set(payload["ground_truth"]) == {
        "expected_outcome",
        "primary_causes",
        "excluded_causes",
        "required_facts",
    }
    assert "agent-id-hidden" not in json.dumps(calls)
    assert "C01" not in json.dumps(calls)
    assert "previous_response_id" not in calls[0]
    assert calls[0]["timeout"] == 20 and calls[0]["store"] is False


def test_live_adapter_has_zero_sdk_retries_and_separate_schema(monkeypatch):
    from evaluation import judge as module

    sdk = MagicMock()
    sdk.return_value.__enter__.return_value.responses.create.return_value = (
        judge_response()
    )
    monkeypatch.setattr(module, "OpenAI", sdk)
    instance = Judge(
        JudgeConfig("judge-model", api_key="synthetic-test-key"),
        load_bundle(),
        load_pricing(),
    )
    assert instance.grade("local-id", "C01", diagnosis())["judge_status"] == "judged"
    assert sdk.call_args.kwargs["max_retries"] == 0
    assert sdk.call_args.kwargs["timeout"] == 20
    assert "synthetic-test-key" not in repr(instance.config)
    for timeout in (0, float("inf"), float("nan")):
        with pytest.raises(Exception):
            JudgeConfig("x", timeout_seconds=timeout)


@pytest.mark.parametrize(
    "label,outcome,expected",
    [
        ("correct", "diagnosed", "correct"),
        ("review_required", "diagnosed", "review_required"),
        ("incorrect", "diagnosed", "incorrect"),
        ("correct", "escalate", "incorrect"),
        ("review_required", "escalate", "incorrect"),
    ],
)
def test_semantic_outcome_priority_and_missing_facts_are_reference_only(
    label, outcome, expected
):
    instance = Judge(
        JudgeConfig("fake"),
        load_bundle(),
        load_pricing(),
        create=lambda **kw: judge_response(label, missing_facts=["결제 이력 생략"]),
    )
    result = instance.grade("local", "C01", diagnosis(outcome))
    assert result["semantic_verdict"] == label and result["final_verdict"] == expected
    assert result["missing_facts"] == ["결제 이력 생략"]
    assert result["outcome_match"] == (outcome == "diagnosed")


@pytest.mark.parametrize("bad", ["timeout", "json", "schema", "refusal"])
def test_judge_failure_keeps_pending_even_outcome_mismatch(bad):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if bad == "timeout":
            raise TimeoutError("sensitive error never persisted")
        if bad == "schema":
            return judge_response("invalid-label")
        if bad == "refusal":
            return response(
                [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]
            )
        return response(
            [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "not json"}],
                }
            ]
        )

    instance = Judge(JudgeConfig("fake"), load_bundle(), load_pricing(), create=create)
    result = instance.grade("local", "C01", diagnosis("escalate"))
    assert result["outcome_match"] is False
    assert (
        result["judge_status"] == "judge_failed"
        and result["final_verdict"] == "pending"
    )
    assert result["semantic_verdict"] is None and len(calls) == 1
    assert "sensitive" not in json.dumps(result)
    assert instance.grade("local", "C01", None)["final_verdict"] == "not_applicable"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "case,summary,causes,label,details",
    [
        ("C01", "일반 구성원은 초대 불가", ["구성원 권한"], "correct", {}),
        (
            "C05",
            "권한과 인원 한도 문제",
            ["권한 부족", "한도 도달"],
            "incorrect",
            {"missing_causes": ["subscription_expired"]},
        ),
        (
            "C01",
            "권한 부족과 결제 실패",
            ["권한 부족", "결제 실패"],
            "incorrect",
            {"incorrect_causes": ["payment_failed"]},
        ),
        (
            "C04",
            "현재 정보로는 확정할 수 없어 오류 메시지가 필요합니다.",
            [],
            "correct",
            {"missing_facts": ["정상 조건 나열 생략"]},
        ),
        (
            "C04",
            "서버 장애입니다.",
            ["서비스 장애"],
            "incorrect",
            {"incorrect_causes": ["confirmed_service_outage"]},
        ),
        (
            "C02",
            "한도 도달과 member 권한 부족",
            ["한도 도달", "권한 부족"],
            "incorrect",
            {"reason": "요청자는 member라는 주장이나 GT는 admin으로 모순된다."},
        ),
    ],
)
def test_fake_semantic_contract_preserves_details_not_actual_meaning_accuracy(
    case, summary, causes, label, details
):
    instance = Judge(
        JudgeConfig("fake"),
        load_bundle(),
        load_pricing(),
        create=lambda **kw: judge_response(label, **details),
    )
    result = instance.grade(
        "local",
        case,
        diagnosis(
            "needs_information" if case == "C04" else "diagnosed",
            summary=summary,
            causes=causes,
        ),
    )
    assert result["final_verdict"] == label
    for key, value in details.items():
        assert result[key] == value


def test_initial_calibration_never_fabricates_human_labels():
    calibration = load_calibration()
    before = calibration.model_dump()
    instance = Judge(
        JudgeConfig("fake"),
        load_bundle(),
        load_pricing(),
        create=lambda **kw: judge_response(),
    )
    report = run_calibration(instance, calibration)
    assert len(calibration.examples) == 15
    assert all(e.human.label is None for e in calibration.examples)
    assert (
        report["status"] == "uncalibrated" and report["agreement"]["denominator"] == 0
    )
    assert calibration.model_dump() == before


@pytest.mark.parametrize(
    "issue",
    [
        None,
        "missing_human",
        "failure",
        "low_agreement",
        "critical_false_correct",
        "unreviewed",
    ],
)
def test_calibration_gates_denominator_confusion_and_disagreement_text(issue):
    calibration = reviewed_calibration()
    labels = [example.human.label for example in calibration.examples]
    if issue == "missing_human":
        calibration.examples[0].human.label = None
    if issue == "unreviewed":
        calibration.examples_reviewed = False
    if issue == "low_agreement":
        labels[0:2] = ["incorrect"] * 2
    if issue == "critical_false_correct":
        labels[5] = (
            "correct"  # 14/15 is >90%, but critical false-correct rejects calibration
        )
    index = [0]

    def create(**kw):
        i = index[0]
        index[0] += 1
        if issue == "failure" and i == 0:
            raise TimeoutError
        return judge_response(labels[i])

    instance = Judge(JudgeConfig("fake"), load_bundle(), load_pricing(), create=create)
    result = run_calibration(instance, calibration)
    assert result["status"] == ("calibrated" if issue is None else "uncalibrated")
    assert result["agreement"]["denominator"] == (
        14 if issue == "missing_human" else 15
    )
    assert len(result["items"]) == 15
    assert result["items"][5]["example"]["diagnosis"]["summary"]
    if issue == "failure":
        assert result["agreement"]["numerator"] == 14 and result["judge_failures"] == 1
        assert result["confusion_matrix"]["correct"]["judge_failed"] == 1
    if issue == "critical_false_correct":
        assert (
            result["critical_false_correct"] == 1 and result["agreement"]["value"] > 0.9
        )
    if issue is None:
        assert (
            applicable_calibration(result, instance, [result["items"][0]["judgment"]])[
                "status"
            ]
            == "calibrated"
        )
        for version in ("judge_model", "prompt_version", "rubric_version", "gt_sha256"):
            changed = deepcopy(result)
            changed["versions"][version] = "new"
            assert (
                applicable_calibration(changed, instance, [])["status"]
                == "uncalibrated"
            )
        changed_judgment = deepcopy(result["items"][0]["judgment"])
        changed_judgment["response_model"] = "new-response"
        assert (
            applicable_calibration(result, instance, [changed_judgment])["status"]
            == "uncalibrated"
        )
