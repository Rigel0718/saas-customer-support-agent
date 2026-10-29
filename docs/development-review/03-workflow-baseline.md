# 03. Workflow Baseline — 비교 가능한 고정 실행과 공통 Runtime

[전체 흐름](README.md) · 이전: [Support Agent](02-support-agent.md) · 다음: [Evaluation](04-evaluation.md)

## Context — Agent가 있어도 Baseline이 필요한 이유

Issue#2는 동적 진단이 작동함을 보여 줬지만 그것이 고정 조회보다 적합한지는 답하지 못했다. Issue#3의 [Proposal](../../openspec/changes/archive/2026-10-22-workflow-baseline/proposal.md)은 같은 Service·데이터·출력 검증·저장 계약을 사용하면서 다음 조회를 Application이 결정하는 비교 대상을 정의했다. 규칙만으로 정답을 계산하는 구현이 아니라 **고정 조회 + 단일 LLM 진단**이다.

## Requirements — 첫 고정 기준선의 범위를 좁힌다

[workflow-diagnosis delta](../../openspec/changes/archive/2026-10-22-workflow-baseline/specs/workflow-diagnosis/spec.md)는 문의 내용에 무관한 일곱 조회, 추가 Tool 요청 없는 최대 한 번의 LLM, 같은 결과·근거·권한·실패 계약을 요구했다. [support-requests delta](../../openspec/changes/archive/2026-10-22-workflow-baseline/specs/support-requests/spec.md)는 mode 선택과 영속화를 추가했다.

문의 유형 Router, Case별 Workflow, 병렬 조회, Workflow Engine/DSL, 평가 Dataset·채점·통계는 제외했다. 처음부터 정교한 Workflow를 만들기보다 이해하고 측정할 수 있는 첫 Baseline을 고정했다. 평가 뒤 유리하도록 Baseline을 바꾸지 않는다는 [Design](../../openspec/changes/archive/2026-10-22-workflow-baseline/design.md)의 제약도 있다.

## Design / Implementation — 순서를 고정하고 업무 판단을 LLM에 남긴다

[run_workflow](../../app/workflow/runner.py)의 정상 경로는 다음과 같다.

| 순서 / step | 실제 조회 | 대상으로 삼는 값 |
|---|---|---|
| 1 | `get_user` | 문의 생성자 UUID |
| 2 | `get_organization` | 인증 컨텍스트 조직 |
| 3 | `get_subscription` | 같은 조직 |
| 4 | `get_payment_history` | 같은 조직 |
| 5~7 | `search_policy` 세 번 | `플랜` → `권한` → `구독 결제` |
| 8 | `call_model` 한 번 | 문의·기준 UTC·일곱 관찰·sequence, strict Diagnosis Schema |

정책을 직접 Prompt에 삽입하는 우회 대신 Agent와 같은 검색 Service를 호출한다. LLM 요청에는 Tool 정의를 제공하지 않으며 function_call을 반환하면 `invalid_output`이다. Workflow도 [execute_observed_tool](../../app/diagnosis/tools.py)을 거치므로 고정 조회가 tool_result로 기록되지만 call_id는 null이다. step=1~7의 조회 단계와 Agent의 모델 turn step은 의미가 다르며 전체 근거 연결은 sequence로 한다.

`not_found`, 빈 결제/정책 결과, expired·past_due·failed/pending 업무 상태는 관찰로 보존하고 다음 단계로 간다. 내부/정책 로딩 오류, DB 오류, Budget 소진은 즉시 중단한다. 부재를 기술 장애로 바꾸지 않으며 출력 교정·추가 조회·재시도는 없다. `needs_information`/`escalate`도 유효하면 completed다.

[diagnose_request](../../app/services/support.py)가 두 Runner 중 하나를 선택하며 선점·상태·결과 검증·저장은 공유한다. API는 기존 경로의 `?mode=workflow`, 생략은 agent다. `execution_mode`를 running claim과 같이 저장하여 실행 중에도 방식을 조회할 수 있다. 이미 완료한 같은 mode만 결과 재사용, 다른 mode는 409다. 따라서 비교에는 같은 문의 내용의 **별도 SupportRequest**가 필요하다.

[Migration 001](../../migrations/001_workflow_execution_mode.sql)은 기존 pending을 null, 기존 running/completed/failed를 agent로 backfill하고 제약을 추가한다. `create_all`은 기존 열을 추가하지 못하므로 신규 DB 초기화와 기존 DB 이행을 구분했다. Trace에만 mode를 남기는 대안은 실행 중 확인·완료 재호출 계약에 부족했고, 별도 endpoint는 인증·상태 API를 중복시켜 제외했다.

### 같게 유지한 조건과 보존한 차이

| 항목 | 공유하는 조건 | 실제 차이 |
|---|---|---|
| 데이터·권한 | Customer/Policy Service, 조직 경계, 필드·정렬·원본 정책 | Agent는 같은 조직의 추가 사용자도 선택 가능. Workflow는 생성자와 인증 조직 고정 |
| 진단 | 같은 모델 설정·DiagnosisDTO·근거 검증·읽기 전용 제약 | Prompt·입력 관찰·문맥 구성은 방식별로 다름 |
| 실행 | 기본 Budget 60초, SDK retry 0, DB timeout·실패 저장 | Agent는 모델 8회/Tool 16회 한도, Workflow는 최대 1회/7회 |
| 측정 원천 | 같은 Trace·usage·상태 의미 | 실제 조회 순서·횟수·LLM 반복은 강제로 맞추지 않음 |

“같은 데이터”는 조회 가능한 원천과 권한이 같다는 뜻이다. 모델에 실제 전달된 관찰 집합까지 같게 강제하면 비교하려는 실행 차이가 사라진다. 따라서 이후 결과는 이 두 구현의 **시스템 수준 Trade-off**이며 동적 Tool 선택 하나의 인과 효과를 분리한 실험이 아니다.

## 후속 Change — 공유 기반을 Agent 밖으로 이동

Workflow 구현 후 [share-diagnosis-runtime Proposal](../../openspec/changes/archive/2026-10-22-share-diagnosis-runtime/proposal.md)은 Workflow가 Agent 헬퍼에 의존하고 Budget·오류·Trace가 중복되는 문제를 다뤘다. 이 Change는 새 외부 capability 없이 [skip_specs: true](../../openspec/changes/archive/2026-10-22-share-diagnosis-runtime/.openspec.yaml)로 진행했다.

| 공통 모듈 | 책임 | 계속 실행기에 남긴 책임 |
|---|---|---|
| [runtime.py](../../app/diagnosis/runtime.py) | `ExecutionBudget`, `ExecutionFailure`, `add_trace` | 어느 설정을 쓰고 언제 반복·종료할지 |
| [tools.py](../../app/diagnosis/tools.py) | 인자 검증·한 번의 조회·timeout·관찰/Trace | 동적 선택·시도 한도 또는 고정 단계 목록 |
| [llm.py](../../app/diagnosis/llm.py) | 한 번의 Responses I/O·usage·오류/Trace | Agent의 call_id/이력, Workflow의 Tool 요청 거부 |

공통 Runner 상속·Protocol·mode 엔진을 만들지 않아 “다음 행동을 누가 선택하는가”가 두 함수에 드러난다. 공통 Runtime은 commit하거나 실행 전략을 고르지 않는다. [Runtime Design](../../openspec/changes/archive/2026-10-22-share-diagnosis-runtime/design.md)은 당시 두 최종 INSTRUCTIONS의 공백·문장 순서까지 보존하고 Prompt 중복을 허용했다.

**현재 코드와 당시 요구의 차이:** `723a9da`에서 공통 [OUTPUT_CONTRACT](../../app/diagnosis/prompts.py)를 삽입해 Prompt가 후속 변경됐다. 따라서 현재 [Prompt snapshot 테스트](../../tests/test_diagnosis_prompts.py)는 현재 기대 문자열을 검증하며 Runtime 분리 전 문자열의 영구 불변을 증명하지 않는다. Runtime 분리 자체와 이후 Prompt 보완을 같은 변경으로 설명하지 않는다. 별도의 출력 계약 delta spec과 변경 선택 근거는 확인 범위에서 미확인이다.

## Verification — 고정 흐름과 공통 조건을 각각 검증

| 요구사항 | Spec/설계 근거 | 구현 | 검증 근거 |
|---|---|---|---|
| 정확한 고정 조회·단일 LLM | [workflow-diagnosis](../../openspec/specs/workflow-diagnosis/spec.md) | `run_workflow` | [test_workflow_runner.py](../../tests/test_workflow_runner.py): `test_fixed_steps_and_shared_observations`, `test_single_model_failure`, `test_fatal_errors_stop_without_model` |
| mode·한 번 실행·경합 | [support-requests](../../openspec/specs/support-requests/spec.md) | Support Service·execution_mode | [test_workflow_execution.py](../../tests/test_workflow_execution.py): `test_mode_recall_contract`, [test_support_execution.py](../../tests/test_support_execution.py): `test_concurrent_claim_and_running_reads` |
| 기존 DB 보존 | Workflow Design의 migration | Migration 001 | [test_workflow_migration.py](../../tests/test_workflow_migration.py): `test_migration_preserves_legacy_data` |
| 공통 조건과 실제 차이 | Workflow Design D4 | 동일 Service/Schema/모델과 별도 요청 | test_workflow_execution의 `test_shared_evaluation_conditions_preserve_execution_differences` |
| 공통화 후 계약 유지 | [Runtime Tasks](../../openspec/changes/archive/2026-10-22-share-diagnosis-runtime/tasks.md) | diagnosis 세 모듈과 두 Runner | [test_diagnosis_runtime.py](../../tests/test_diagnosis_runtime.py), Prompt 테스트, [Workflow deadline](../../tests/test_workflow_deadline.py)·Agent 회귀 |

두 Change의 Tasks는 자동 검증 완료로 표시돼 있으나 각 Change만의 독립적인 통과 건수는 이 문서에서 새로 추정하지 않는다. 후속 Evaluation Runbook은 당시 기존 회귀 338개를 포함한 전체 425개 통과를 기록한다. 이는 후속 시점의 전체 결과다.

2026-10-22 [실제 Workflow Smoke](../../.smoke-results/workflow-run-tlgjbjw2/report.md)는 `completed/diagnosed`, 조회 7회·LLM 1회, 응답 모델 `gpt-5-mini-2025-08-07`을 보여 준다. [Trace](../../.smoke-results/workflow-run-tlgjbjw2/traces.json)에서도 고정 순서와 종료 저장을 확인했다. 사용자 의미 검토는 Pass이며 정상 실행이므로 실패 경로는 미검증이다. 실행 당시 로컬 변경이 있어 Backend revision은 미검증이라고 보고서가 명시한다. Agent Smoke와 별도 시각의 한 번 실행이므로 두 Smoke의 시간·Token으로 성능 비교하지 않는다. 이번 조사에서 재실행하지 않았다.

## Outcome — 비교 대상과 관찰 원천을 확보

Workflow는 조회 완전성을 Application이 확보하는 대신 단순 문의에도 전체 조회를 수행한다. Agent는 필요한 범위를 선택할 수 있지만 탐색·종료 판단 부담을 가진다. 이 차이를 측정할 두 실행기와 공통 Trace를 확보했으며, 의미 정확도·조회 필요성·비용 환산은 아직 별도 작업이었다. 다음 [Evaluation](04-evaluation.md)은 GT·동결 조건·반복·분모를 추가해 실행 기록을 비교 근거로 바꿨다.
