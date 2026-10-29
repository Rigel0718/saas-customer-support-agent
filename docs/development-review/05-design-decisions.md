# 05. 주요 Design Decisions

[전체 흐름](README.md) · [Backend](01-backend-foundation.md) · [Agent](02-support-agent.md) · [Workflow](03-workflow-baseline.md) · [Evaluation](04-evaluation.md)

아래 선택지는 실제 Design·코드·결과에 등장하는 것만 정리했다. 문서에 없는 대안을 사후에 발명하지 않았으며, 변경 사실은 확인돼도 채택 이유가 충분하지 않으면 근거 미확인으로 표시했다.

## D1. 진단 선택과 권한·실행 집행을 분리한다

| 항목 | 복원한 내용 |
|---|---|
| 당시 문제 | HTTP와 LLM Tool이 별도 데이터 접근 규칙을 갖거나, 모델의 ID·역할을 신뢰하면 조직 경계가 달라질 수 있다. |
| 확인된 선택지 | 기존 조회 함수 재사용, Service를 클래스/Repository로 재작성. Backend Design은 비동기 드라이버·범용 Repository도 검토 후 제외했다. |
| 실제 채택 | Application이 DB 사용자로 CallerContext를 만들고 Service가 조직을 검사한다. Agent는 읽기 Tool 선택·진단, Support Service는 실행 선점·검증·commit을 소유한다. |
| 이유·Trade-off | 같은 Service·DTO·오류를 재사용하고 불필요한 추상화를 피한다. 내부 Python 컨텍스트가 신뢰 경계이며 타입 검사만으로 위조를 막는 것은 아니다. 같은 조직의 지원 문의 공유는 MVP 권한 범위다. |
| 구현·검증 근거 | [Backend Design 1/3](../../openspec/changes/archive/2026-10-14-backend-foundation/design.md), [Agent Design D2/D3](../../openspec/changes/archive/2026-10-22-support-agent/design.md), [Customer Service](../../app/services/customer.py), [test_policy_tools](../../tests/test_policy_tools.py)·[test_backend](../../tests/test_backend.py) |
| 이후 결과·한계 | Agent/Workflow가 같은 조직·같은 필드를 조회하는 기반이 됐다. 실제 변경·환불은 수행하지 않는다. 개발 Bearer 인증은 운영 세션·만료/회전을 제공하지 않는다. |

## D2. 유한 실행은 Budget과 명시적 실패로 통제한다

| 항목 | 복원한 내용 |
|---|---|
| 당시 문제 | 모델 반복·잘못된 Tool·느린 I/O가 끝나지 않거나 실패를 숨긴 재시도로 바뀌면 실행·비용·비교 조건을 통제할 수 없다. |
| 확인된 선택지 | 초기 엄격한 전체 deadline 목표와 실제 LLM I/O 취소+유한 DB timeout. [수정 기록](../../study/support-agent-fixes.md)은 DB 취소 경계 변경/연결 선확보를 후속 선택으로만 남겼다. |
| 실제 채택 | Agent 8 LLM steps/16 Tool 시도·기본 60초 Budget, Workflow 7조회/1 LLM·기본 60초. LLM I/O 취소, 요청 Timeout, SDK retry 0, 출력 교정 재호출 없음. |
| 이유·Trade-off | 현재 동기 Session 구조에서 검증된 취소 범위를 채택했다. 동기 SQL·pool/connect·파일/CPU·종료 저장까지 엄격히 60초로 제한하지 않는다. 네트워크 취소도 공급자 청구 중단을 보장하지 않는다. |
| 구현·검증 근거 | [Agent Tasks 5.3/검증 기록](../../openspec/changes/archive/2026-10-22-support-agent/tasks.md)의 계약 확정, [ExecutionBudget](../../app/diagnosis/runtime.py), [responses_create](../../app/diagnosis/llm.py), [test_agent_deadline](../../tests/test_agent_deadline.py), [test_workflow_deadline](../../tests/test_workflow_deadline.py) |
| 이후 결과·한계 | 초기 Pilot에서 limit_exceeded/llm_error를 구분했다. 신뢰성 Change는 요청 Timeout을 설정 가능하게 했지만 기본값을 자동 상향하지 않았다. 본 평가의 30초는 해당 run의 설정이며 원인 누락 해결책은 아니다. |

Fail-fast는 모든 조회 부재를 실패시킨다는 뜻이 아니다. not_found·invalid_arguments·unknown_tool은 Agent가 판단할 수 있는 관찰이고, Workflow도 부재·빈 결과는 계속한다. 치명적 내부 오류·DB 오류·Budget/한도·유효하지 않은 최종 결과는 추가 교정 없이 종료한다.

## D3. 실행 상태와 결과·Trace 저장은 Application이 원자적으로 소유한다

| 항목 | 복원한 내용 |
|---|---|
| 당시 문제 | 동시 diagnose 중복, 인증 SELECT의 autobegin, LLM 실행 뒤 저장 실패, commit 응답 유실을 구별해야 한다. |
| 확인된 선택지 | 생성과 자동 진단 결합, Tool별 Session/별도 저장 Session, 현재 주입 Session의 명시적 단계 commit. |
| 실제 채택 | 문의 생성·진단 분리, 조건 UPDATE로 running 선점 후 commit, 실행 중 메모리 Trace, 종료 상태·Diagnosis·Trace 일괄 commit. DB 실패 rollback 후 재조회·한 번 실패 저장 시도. |
| 이유·Trade-off | 실행 실패에도 문의를 보존하고 Tool/조회가 transaction을 소유하지 않게 한다. 한 Session으로 단순화하는 대신 LLM 대기 중 읽기 트랜잭션/연결이 유지되고 중도 Trace는 durable하지 않다. |
| 구현·검증 근거 | [Agent Design D1/D6](../../openspec/changes/archive/2026-10-22-support-agent/design.md), [diagnose_request/save_terminal](../../app/services/support.py), [test_support_execution](../../tests/test_support_execution.py)의 경합·commit 장애·완료 결과 보호 |
| 이후 결과·한계 | 두 방식이 같은 실패·재호출 계약을 쓴다. 프로세스 종료/지속 DB 장애는 running·Trace 유실을 남길 수 있고 자동 복구는 없다. READ COMMITTED는 전체 실행의 동일 스냅샷을 보장하지 않는다. |

## D4. Workflow는 고정 전체 조회를 수행하는 첫 Baseline으로 만든다

| 항목 | 복원한 내용 |
|---|---|
| 당시 문제 | Agent의 작동만으로 동적 선택의 비용과 효용을 판단할 수 없다. |
| 확인된 선택지 | 전체 고정 조회와 문의 분류/복잡한 분기. Workflow Design은 첫 기준선을 이해·검증 가능하게 유지하기 위해 분류·복잡한 분기를 제외했다. |
| 실제 채택 | 고객 4조회·정책 3검색 후 단일 LLM. 같은 Service·원본 정책·모델·Schema·저장·기본 Budget을 사용하고 Prompt/실제 관찰/호출 수는 방식대로 보존한다. |
| 이유·Trade-off | 확인 범위를 Application이 보장하고 제어 흐름을 명확히 한다. 단순 문의에도 과잉 조회하며 문의에 언급된 다른 사용자를 자동 조회하지 않는다. Workflow 전체를 대표하는 최적 구조는 아니다. |
| 구현·검증 근거 | [Workflow Design D1/D4](../../openspec/changes/archive/2026-10-22-workflow-baseline/design.md), [run_workflow](../../app/workflow/runner.py), [공통 조건 테스트](../../tests/test_workflow_execution.py)의 `test_shared_evaluation_conditions_preserve_execution_differences` |
| 이후 결과·한계 | 본 표본에서 Workflow의 의미 오답은 없었고 지연/비용도 더 낮았다. 고정 전체 조회·단일 호출이 기여했다는 것은 그 결과에 대한 해석이다. 동적 Tool 선택만의 인과 효과나 모든 업무에서의 우열로 일반화할 수 없다. |

## D5. 공통 Runtime은 한 번의 실행을 공유하고 다음 행동은 공유하지 않는다

| 항목 | 복원한 내용 |
|---|---|
| 당시 문제 | Workflow가 Agent 헬퍼를 import하고 Budget·LLM/Tool·Trace 처리가 중복되면 소유권과 평가 조건이 흔들린다. |
| 확인된 선택지 | Agent 모듈에서 계속 재사용, 공통 Runner 상속/Protocol/엔진, 작은 중립 헬퍼 분리. |
| 실제 채택 | diagnosis/runtime·tools·llm에 단일 호출/관찰/제한을 분리. Agent에는 동적 반복/call_id/한도, Workflow에는 고정 순서/단일 호출/Tool 거부를 유지했다. |
| 이유·Trade-off | 공통 실패·관찰 계약을 한 곳에서 유지하면서 두 알고리즘의 차이를 숨기지 않는다. 당시 Prompt는 중복을 허용하고 최종 문자열을 보존했다. |
| 구현·검증 근거 | [Runtime Design D1~D6](../../openspec/changes/archive/2026-10-22-share-diagnosis-runtime/design.md), [diagnosis](../../app/diagnosis/), [Runtime 테스트](../../tests/test_diagnosis_runtime.py), [Prompt 테스트](../../tests/test_diagnosis_prompts.py) |
| 이후 결과·한계 | `9d2a5bb`에 Workflow와 Runtime 코드가 함께 반영돼 별도 Commit 경계는 없다. 후속 `723a9da`의 OUTPUT_CONTRACT 삽입은 실제 Prompt 변경이다. 공통화 효과나 진단 품질 개선으로 혼동하지 않는다. 해당 후속 변경의 독립 delta spec·선택 이유는 근거 미확인이다. |

## D6. Complete Diagnosis와 Blind Judge를 사용하고 사람 예제로 검토한다

| 항목 | 복원한 내용 |
|---|---|
| 당시 문제 | 자연어 동의 표현·복수 원인·정보 부족은 문자열 일치만으로 채점하기 어렵다. 구조상 completed라도 정답은 아닐 수 있다. |
| 확인된 선택지 | 문자열/단어 검사, Agent/Workflow 쌍 비교 Judge, GT와 개별 결과의 의미 비교. Evaluation Design은 앞의 두 방식에 오채점·모드/위치 편향 위험을 명시했다. |
| 실제 채택 | GT와 summary+causes를 독립 Blind Judge에 주고 outcome은 Python에서 검사한다. 모든 필수 원인·추가 오답 부재를 요구한다. 15개 사람 기준 예제로 버전별 Calibration한다. |
| 이유·Trade-off | 원인 ID를 제품 출력에 강제하지 않고 동의 표현을 허용한다. next_actions/evidence·Groundedness·Task Success는 채점하지 않는다. 같은 계열 모델의 편향·작은 Calibration Set 한계가 남는다. |
| 구현·검증 근거 | [Evaluation Design D2/D6/D7](../../openspec/changes/archive/2026-10-29-agent-workflow-evaluation/design.md), [Judge](../../evaluation/judge.py), [GT](../../evaluation/data/invite_gt.json), [Judge 테스트](../../tests/test_evaluation_judge.py), [저장 Calibration](../../.evaluation-results/calibration-v2.json) |
| 이후 결과·한계 | v2는 15/15 일치하나 review_required 유형은 미검증이다. C05 Agent의 구독 미조회·만료 원인 누락은 completed와 의미 정답의 차이를 보여 줬다. 모든 조건을 진단해야 한다는 기준을 결과에 맞춰 사후 수정하지 않았다. |

## D7. 관측 누락과 실행 실패를 숨기지 않고 보존한다

| 항목 | 복원한 내용 |
|---|---|
| 당시 문제 | 성공 표본만 평균내거나 usage/Trace 누락을 0으로 바꾸면 비용·안정성 판단이 왜곡된다. 초기 오류는 코드 하나만 남아 원인 분석도 어려웠다. |
| 확인된 선택지 | LLM 메타데이터를 Tool observation/별도 로그에 저장, 실패 전용 이벤트/중복 metadata, 기존 Trace에 nullable 필드 추가. 각 관측성 Design에서 대안을 명시했다. |
| 실제 채택 | all_started/successful 분리, null/observed/missing/censored와 분모 보존. llm_response에는 안전한 llm_metadata, 출력 거부는 단일 final_result.failure_metadata. 원본 네 JSON과 source hash가 연결된 별도 재채점. |
| 이유·Trade-off | 진단 근거와 기술 실패를 구분하고 원문·Credential 노출 경로와 중복 집계를 줄인다. 상세 원문 재현과 과거 실패 복원은 제공하지 않는다. 파일별 atomic 저장은 다중 파일 전체 원자성이 아니다. |
| 구현·검증 근거 | [Evaluation Design D5/D8](../../openspec/changes/archive/2026-10-29-agent-workflow-evaluation/design.md), [신뢰성 Design](../../openspec/changes/archive/2026-10-29-improve-llm-execution-reliability/design.md), [실패 관측성 Design](../../openspec/changes/archive/2026-10-29-diagnosis-failure-observability/design.md), [metrics](../../evaluation/metrics.py)·[results](../../evaluation/results.py) 및 대응 테스트 |
| 이후 결과·한계 | 과거 invalid_output 세 건은 세부 사유 미확정이다. 후속 metadata는 새 실패의 거부 경계를 제공할 뿐 실패율·정확도 개선 실측이 아니다. 신규 Reader는 과거 누락 필드를 읽지만 과거 strict Reader가 새 필드를 읽는 것은 보장하지 않는다. |

## D8. 현재 정형 초대 진단에는 Workflow를 우선한다

| 항목 | 복원한 내용 |
|---|---|
| 당시 문제 | “Agent가 동적이므로 낫다”는 가설을 실제 아키텍처 선택 근거로 바꿔야 했다. |
| 확인된 선택지 | 현재 두 구현의 선택. Hybrid Routing·Case-specific Workflow·병렬 호출은 후속 후보로만 문서에 등장하며 실험한 대안은 아니다. |
| 실제 채택 | 확인 조건이 적고 알려져 있으며 Complete Diagnosis가 필요한 현재 범위는 Workflow 우선. |
| 이유·Trade-off | 실행 성공 24/25 대 23/25, 시작 대비 완전한 정답 24/25 대 22/25, 더 낮은 평균 지연·추정 비용. Agent는 조회 수를 줄였으나 반복 호출 비용과 C05의 원인 누락이 관찰됐다. |
| 구현·검증 근거 | [실측 원본](../../.evaluation-results/invite-50-v1/), [1차 결론](../evaluation/first-evaluation-conclusion.md), [이번 원본 대조와 분모 설명](04-evaluation.md) |
| 이후 결과·한계 | 5개 Case×5회, 현재 모델/Prompt/제어 방식의 시스템 수준 판단이다. 통계적 유의성·동적 선택의 인과 효과·광범위한 Agent 효용 부재를 주장하지 않는다. 적용할 Case가 다양해질 때 탐색의 이득은 별도 Evaluation이 필요하다. |

## 이력 회수용 Commit

| Commit | 복원할 때 유용한 변경 |
|---|---|
| `4212241` / `e8782dc` / `d361dfd` | 최초 Backend, 패키지 구조, 완료 정리 |
| `854910e` / `b0ac208` | Agent 구현·테스트, 실제 Smoke 지원과 Seed 상대 날짜 계약 |
| `9d2a5bb` | Workflow·mode/migration·공통 Runtime 반영 |
| `bf64790` | Evaluation Framework v0.1 |
| `723a9da` | Calibration v2, 요청 Timeout/LLM metadata, OUTPUT_CONTRACT; 본 평가의 clean revision |
| `4e7cf42` | 본 평가 이후 출력 실패 관측성 |
| `21c4bac` | 1차 결론, main Spec 동기화와 남은 Change Archive |

위 관계는 `git log`/`git show`로 확인했다. Commit 메시지가 모든 세부 결정을 설명하지는 않으며, 여러 Change가 함께 반영된 Commit을 엄밀한 단계별 인과 실험으로 해석하지 않는다.
