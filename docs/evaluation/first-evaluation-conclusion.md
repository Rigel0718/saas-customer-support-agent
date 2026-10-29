# Agent vs Workflow — 1차 Evaluation 결론

작성일: 2026-10-29 · Issue #4

현재의 정형적인 팀원 초대 실패 진단에서는 **Workflow를 우선 선택한다.** 필요한 조회 범위가 작고 정해져 있으며, 모든 필수 원인을 확인하는 Complete Diagnosis가 성공 기준이기 때문이다. 이번 표본에서 Workflow는 Agent보다 높은 실행 성공률과 정답 비율, 낮은 평균 지연과 추정 비용을 보였다. 이 결정은 현재 두 구현과 다섯 Case에 한정한다.

## 1. 실험 범위와 근거

- 대상: 동일한 초대 실패 문의의 C01~C05 × Agent/Workflow × 5회, 총 50회(모드별 25회).
- 원본: 로컬 `.evaluation-results/invite-50-v1/{manifest,executions,judgments,summary}.json`. 결과 디렉터리는 Git 추적 대상에서 제외되어 있으므로 원본은 별도로 보존해야 한다.
- Run ID: `ffcd91b5-8300-4b97-8c33-05419e92de4f`.
- 실행일: 2026-10-27, 09:50:02~10:07:21 UTC. `status=collected`, `completed_count=50`, `valid_comparison=true`, `quality_conclusion_ready=true`.
- 실행 코드: `723a9da6e06b737c9404ead3c7eb3f399f4003be`, 실행 당시 `dirty=false`. 이후 관측성 Commit `4e7cf42`의 품질 개선 측정 결과로 해석하지 않는다.
- 데이터/GT: `invite-v1` / `invite-gt-v1`. 동일 Fixture·정책·Service Layer를 사용하고 매번 새 문의를 생성했다. 홀수 반복은 Agent→Workflow, 짝수 반복은 Workflow→Agent 순서다.
- 설정 모델: 두 모드 모두 `gpt-5-mini`, 실제 응답 모델 `gpt-5-mini-2025-08-07`. 요청 Timeout 30초, 모드별 실행 Budget 60초, Agent 최대 8 steps / 16 Tool Calls. Workflow는 고정 조회 7회와 LLM 1회를 사용한다.

이 문서는 저장된 결과를 읽어 정리한 기록이다. 평가·채점·Calibration을 재실행하거나 기존 결과, Golden Set, Judge 기준을 수정하지 않았다. 실험 조건과 지표의 상세 계약은 [Evaluation Runbook](../evaluation_runbook.md)을 따른다.

원본 식별용 SHA-256:

| 파일 | SHA-256 |
|---|---|
| manifest.json | `02d45eeda54c1312993280177aae3dd46a48079d30f44a4411b5520427c363d4` |
| executions.json | `9814a8a5e3a13672d18bdebea4749dcbad7c0f0fa790cfb60ef2fa3e63c9a988` |
| judgments.json | `24383fc9d2dc5c074f6a336d9ea87dce9b84b32b78ba3b603d9ff7ecbc57ea35` |
| summary.json | `0b92a89a237a6a5f01c5dda79e0bc5812e7a1164fed28636b528df69c2b686f7` |

## 2. Correctness와 진단 성공의 정의

**Complete Diagnosis**는 GT의 `primary_causes`를 모두 식별하고, 잘못된 추가 원인을 주장하지 않으며, 기대 `outcome`과 일치하는 진단이다. 하나의 차단 원인을 찾는 것으로 완료하지 않는다. 복수 원인이면 현재 확인 가능한 원인을 모두 포함해야 한다. 이는 기존 `cause-correctness-v1` 기준의 해석이며 새로운 채점 기준을 도입한 것이 아니다.

Judge는 모드·Trace·비용·다른 답변을 받지 않고 GT와 `summary + causes`만 의미 비교한다. 자연어 동의 표현을 허용한다. `required_facts`는 맥락이며 모든 사실을 나열할 의무가 없고, `missing_facts`만으로 오답을 강제하지 않는다. 다만 사실에 반하는 주장은 오답 근거가 될 수 있다. `next_actions`에 확인을 권고하는 것만으로 실제 원인을 식별했다고 인정하지 않는다. Python은 `expected_outcome` 일치 여부를 별도로 검사한다.

C04는 관찰된 권한·구독·인원 조건으로 원인을 확정할 수 없으므로 `needs_information`과 추가 정보 필요를 올바르게 표현하면 정답이다. 정상 조건을 전부 나열할 필요는 없으며 근거 없이 장애 등을 확정해서는 안 된다.

세 비율을 구분한다.

- **Execution Success** = 유효한 Diagnosis가 `completed`로 저장된 수 / 시작 실행 수. 의미 오답도 실행 성공일 수 있다.
- **Diagnosis Correctness** = `correct / (correct + incorrect)`. `not_applicable`, `pending`, `review_required`는 분모에서 제외한다.
- **시작 실행 대비 완전한 정답 비율** = `correct / started`. 실행 실패까지 포함해 진단 성공을 해석하기 위한 보조 계산이며, 기존 summary 지표를 대체하거나 추가 저장하지 않는다.

## 3. 정량 결과

| 지표 | Agent | Workflow |
|---|---:|---:|
| 시작 실행 | 25 | 25 |
| 실행 성공 | 23/25 (92%) | 24/25 (96%) |
| 의미 정답 / 의미 오답 | 22 / 1 | 24 / 0 |
| Diagnosis Correctness | 22/23 (95.65%) | 24/24 (100%) |
| 시작 실행 대비 완전한 정답 비율 | 22/25 (88%) | 24/25 (96%) |
| 실행 실패 (`invalid_output`) | 2/25 (8%) | 1/25 (4%) |
| Judge `not_applicable` | 2 | 1 |

양쪽 모두 incomplete·환경 오류·Judge pending·review_required는 0건이다. 전체 50회 중 실행 성공은 47회, 의미 정답은 46회다. Workflow의 Correctness 100%는 실행 실패 1건을 제외한 24건에 대한 값이다.

다음 효율 값은 **실패를 포함한 `all_started`, 모드별 n=25** 기준이다. 각 지표의 missing/censored 수는 0이다. Judge overhead는 제외한다.

| 지표 | Agent | Workflow |
|---|---:|---:|
| 평균 E2E Latency | 23,011.68 ms | 18,224.83 ms |
| 중앙값 E2E Latency | 22,753.23 ms | 17,138.74 ms |
| 평균 LLM Calls | 5.12 | 1.00 |
| 평균 Tool Calls | 4.12 | 7.00 |
| 평균 Input Tokens | 7,976.96 | 1,736.40 |
| 평균 Output Tokens | 2,296.56 | 2,345.88 |
| 평균 Total Tokens | 10,273.52 | 4,082.28 |
| 평균 Estimated Cost (USD) | 0.00658736 | 0.00512586 |
| 합계 Estimated Cost (USD) | 0.16468400 | 0.12814650 |

Workflow는 Agent 대비 평균 지연이 약 20.8%, 추정 비용이 약 22.2%, Total Tokens가 약 60.3% 낮았다. Agent는 Tool 조회가 더 적었지만 반복 LLM 호출과 누적 문맥 비용을 상쇄하지 못했다. 이 관찰만으로 동적 Tool 선택 자체의 인과적 효과를 분리할 수는 없다.

E2E는 문의 생성 직전부터 진단 Service 반환/예외까지이며 종료 저장을 포함한다. 사전 검증·인증·사후 수집·Judge·파일 저장과 HTTP 전송 시간은 포함하지 않는다. 비용은 manifest의 `2026-10-26-v1` 단가표(입력 0.25 USD/백만 Tokens, 출력 2.00 USD/백만 Tokens)를 적용한 당시 추정치이며 캐시·할인·세금이나 실제 청구액을 반영하지 않는다.

Case별로 각 모드의 정답/오답/실행 실패를 합하면 5회다.

| Case | 기대 진단 | Agent 정답 / 오답 / 실행 실패 | Workflow 정답 / 오답 / 실행 실패 |
|---|---|---:|---:|
| C01 | 초대 권한 부족 | 5 / 0 / 0 | 5 / 0 / 0 |
| C02 | 인원 한도 도달 | 5 / 0 / 0 | 5 / 0 / 0 |
| C03 | 구독 만료 | 4 / 0 / 1 | 4 / 0 / 1 |
| C04 | 원인 확정 불가, 추가 정보 필요 | 5 / 0 / 0 | 5 / 0 / 0 |
| C05 | 권한 부족 + 인원 한도 + 구독 만료 | 3 / 1 / 1 | 5 / 0 / 0 |

## 4. LLM Judge 검증

저장된 Calibration v2는 사람 기준 15건과 Judge가 15/15 일치(100%)했고, 지정 핵심 오답의 false-correct와 Judge 기술 실패는 모두 0건이다. `status=calibrated`, `versions_match=true`로 본 평가에 연결되어 있다.

- Calibration: `invite-calibration-v2`, 기준 `invitation-calibration-criteria-v2`.
- Human Label: correct 8건, incorrect 7건, review_required 0건.
- Judge 모델: `gpt-5-mini`; Prompt `invitation-judge-prompt-v1`; Rubric `cause-correctness-v1`; GT `invite-gt-v1`.
- 본 평가에서는 47건을 채점하고 Diagnosis가 없는 3건은 `not_applicable`로 남겼다.

이 검증은 해당 Set·버전에 한정된다. `review_required`의 의미 분류 성능은 미검증이며, 15건의 일치가 일반적인 Judge 정확성 또는 50건 전체의 독립적인 사람 정답 검증을 뜻하지 않는다. C05 오답은 아래와 같이 원본 Diagnosis·조회 Trace·GT·Judge 사유를 대조했다.

## 5. C05 Agent Incorrect 분석

대상은 반복 2회의 실행 `483bc540-2aaf-50bd-b5c9-7f12dcf12ed4`다. `completed`, `outcome=diagnosed`, `outcome_match=true`로 실행과 형식 검증은 성공했지만 의미 판정은 incorrect다.

GT는 `permission_denied`, `member_limit_reached`, `subscription_expired` 세 원인을 모두 요구한다. 실제 Trace에서는 `get_user` → `get_organization` → `search_policy`를 호출했다. member 역할, 현재 인원 2명/한도 2명과 관련 정책을 확인했지만 **`get_subscription`은 호출하지 않았다.** 정책에는 활성·미만료 구독도 초대 조건임이 포함되어 있었다.

최종 `summary + causes`는 권한 부족과 인원 한도만 확정하고 구독은 미확인으로 남겼다. `next_actions`에서 구독 확인을 권고하고 조회 허용 여부를 요청했지만, 실제 만료 상태를 조회하거나 원인으로 식별하지 않았다. Judge의 `missing_causes=[subscription_expired]`, `incorrect_causes=[]`는 이 기록과 일치한다. 결제 이력 등 `missing_facts`의 생략 자체가 오답 사유는 아니다.

따라서 관찰된 실패는 **일부 차단 원인 발견 후 필요한 상태 조회를 마치지 않고 종료한 불완전 진단**이다. 구독 확인이 필요하다는 정책 지식과 실제 상태 확인 사이에 공백이 남았다. 모델 내부의 종료 동기는 알 수 없다. 첫 차단 원인 안내만을 목표로 삼으면 다른 해석이 가능하지만, 이번 실험에서 합의한 Complete Diagnosis에서는 오답이다. GT나 Judge를 사후 변경해 정답으로 바꾸지 않는다.

이 사례는 Agent C05 반복 4회의 `invalid_output`과 구분한다. 후자는 Diagnosis가 없어 의미 채점 대상이 아니다.

## 6. 1차 Design Decision

정형 고객 지원에서 확인해야 할 조건이 적고 알려져 있으며 누락 없는 진단이 중요할 때, 현재의 고정 Workflow를 우선한다. 조회 범위를 Application이 정하므로 권한·조직·구독·결제·정책을 빠짐없이 확보할 수 있고, 이번 관찰에서는 단일 LLM 호출이 더 낮은 지연·Tokens·추정 비용으로 이어졌다.

Agent의 선택적 조회는 평균 Tool Calls를 줄였지만, C05에서 확인 가능한 필수 원인을 누락했다. 현재의 저렴한 읽기 조회 7개를 일부 생략하는 이득보다 LLM 반복과 진단 완전성 관리 부담이 컸다는 해석을 채택한다. 다만 Workflow도 `invalid_output` 1건을 보였으므로 실패가 없는 구조라고 결론 내리지 않는다.

이는 이번 MVP에서의 1차 선택 근거다. 문의별 필요한 정보가 크게 달라지거나 사전 정의하기 어려운 탐색이 필요한 업무에서 Agent가 유리한지는 별도 검증이 필요하다. Hybrid Routing·병렬 Tool 호출·Prompt 개선·새 Workflow는 향후 후보이며 이번 작업에서 구현하거나 성능을 주장하지 않는다.

## 7. 실패 관측성과 미확인 운영 상태

50회에서 관찰한 `invalid_output`은 Agent C03 반복 4회, Agent C05 반복 4회, Workflow C03 반복 5회의 총 3건이다. 당시 구체적인 실패 metadata와 원문이 없어 응답 상태·JSON·Schema·근거 참조 중 어느 단계인지 확정할 수 없다. Token 수나 지연으로 원인을 추정하거나 과거 결과에 backfill하지 않는다.

후속 `diagnosis-failure-observability`는 Commit `4e7cf42`에서 구현됐다. 고정된 실패 단계·사유·선택적 필드 경로를 기존 `final_result.failure_metadata`에 기록하고 Trace 저장/조회 및 실행 단위 집계로 연결한다. 응답/예외 원문을 수집하지 않으며 진단 전략이나 기존 수락·거부 기준은 유지한다. 이는 향후 거부 경계의 관측성을 제공하며, 기존 세 실패의 복원이나 C05 누락 개선·실제 실패율 감소를 입증하지 않는다.

사용자 로컬 검증 기록(2026-10-28)은 저장/API 테스트 **13 passed**, 전체 테스트 **672 passed**, 경고 1건이다. 이번 문서·Archive 작업에서는 테스트를 재실행하지 않았다. 테스트 소유 schema의 Migration 검증과 기존 개발 DB의 운영 상태는 구분한다.

**Migration 003 (`migrations/003_diagnosis_failure_metadata.sql`)이 기존 개발 DB에 실제 적용됐는지는 미확인이다.** 이번 작업은 DB에 연결하거나 Migration을 적용하지 않았다. 기존 DB는 001/002 선행 후 새 코드 실행 전에 003을 수동 적용해야 하며, 테스트 통과나 Change Archive가 그 적용을 증명하지 않는다. 절차는 [Runbook](../evaluation_runbook.md)을 따른다.

## 8. 실험의 한계

- 다섯 개의 고정된 초대 실패 Case를 각각 5회 반복한 작은 표본이다. 50개의 서로 다른 업무나 독립적인 실제 고객 문의를 대표하지 않으며, 통계적 유의성이나 보편적 아키텍처 우열을 주장하지 않는다. MVP 문서의 장기 30 Case 계획을 완료한 것도 아니다.
- 동일 Service·데이터·모델을 사용했지만 Agent/Workflow의 Prompt·문맥 구성·호출 방식이 다르다. 비교는 현재 구현 전체의 시스템 수준 Trade-off다.
- 실행은 순차적이며 홀수/짝수 교차가 시간 영향을 완전히 통제하지 못한다. 반복 5회라 시작 모드 비중도 Agent:Workflow=3:2다. 동시 부하·환경 변동·모델 alias 변경의 영향을 분리하지 않았다.
- Correctness는 `summary + causes`와 outcome에 관한 지표다. 실제 초대 성공, 후속 조치의 완전성, 독립적인 Groundedness·Safety·Tool Selection Quality는 측정하지 않았다. C04에서도 실제 서비스 장애 여부를 확인한 것은 아니다.
- 실행 실패는 Correctness 분모에서 빠지므로 실행 성공률과 시작 대비 완전한 정답 비율을 함께 봐야 한다. 과거 출력 실패의 세부 원인은 미기록이며 이후 관측성 변경의 효과도 재측정하지 않았다.
- Calibration은 15개 예제와 일치하는 버전에 한정되고 review_required 유형은 미검증이다. 비용은 당시 단가표의 추정이며 Judge overhead와 실제 청구액을 포함하지 않는다.
