# SaaS Customer Support Agent 개발 흐름 복원

조사 기준: 2026-10-29, `b2b221e`의 저장소와 로컬 실행 산출물. 이 문서는 Backend부터 1차 Agent vs Workflow Evaluation까지 **요구사항 → 설계 → 구현 → 검증 → 다음 요구사항**의 연결을 복원한다. 새 기능이나 재설계 제안서가 아니다.

## 출발점과 도달점

[MVP](../mvp.md)의 목표는 가상의 B2B SaaS에서 고객 문의를 진단하는 두 구현을 만들고, 어떤 조건에서 Agent 또는 Workflow가 유리한지 실증적으로 판단하는 것이었다. “동적 조회가 복합 문의에서 유리할 수 있다”는 출발 가설이며 확정된 결론이 아니다. 구독·결제·권한·팀원 초대를 다루되 실제 결제, 환불, 계정 변경은 제외했다.

1차 평가에서는 **작고 정형적인 초대 실패 진단과 모든 필수 원인 식별이라는 성공 기준에 대해 현재 Workflow를 우선한다**는 결론을 얻었다. 5개 Case × 2개 방식 × 5회에서 Agent는 22/25, Workflow는 24/25의 완전한 정답을 냈다. 이는 실행 실패를 포함한 시작 실행 기준의 보조 비율이다. 표본·Prompt·실행 구조가 제한되어 Agent/Workflow 일반의 우열을 뜻하지 않는다. 자세한 분모와 원본 검증은 [Evaluation](04-evaluation.md)을 따른다.

## 읽는 순서

| 문서 | 복습할 핵심 질문 |
|---|---|
| [01 Backend Foundation](01-backend-foundation.md) | 인증·조직 경계와 재현 가능한 데이터를 어떻게 공통 기반으로 만들었는가? |
| [02 Support Agent](02-support-agent.md) | LLM의 선택과 Application의 실행·저장 책임을 어떻게 나눴는가? |
| [03 Workflow Baseline](03-workflow-baseline.md) | 무엇을 고정하고 무엇을 공유해야 비교 대상이 되는가? |
| [04 Evaluation](04-evaluation.md) | 무엇을 측정했고, 관찰 결과에서 어디까지 판단할 수 있는가? |
| [05 Design Decisions](05-design-decisions.md) | 중요한 선택의 문제·대안·근거·Trade-off는 무엇이었는가? |

## 실제 Change 순서와 연결

현재 활성 Change는 없고 Archive에 7개 Change가 있다. 아래 순서는 디렉터리 날짜만이 아니라 Proposal의 선행 상태와 Git 이력을 대조한 것이다. Archive 날짜는 구현 날짜와 다르며, 한 Commit에 여러 Change와 보완이 함께 들어가기도 했다.

| 단계 / Change | 요구와 핵심 구현 | 검증 근거와 결과 | 다음 단계로 이어진 이유 |
|---|---|---|---|
| 1. [backend-foundation](../../openspec/changes/archive/2026-10-14-backend-foundation/proposal.md), Issue#1 | 네 고객 모델, 조직 범위 Service, 읽기 API, Seed | PostgreSQL 제약·권한·읽기 불변성 테스트. 당시 설계 요약에 69 passed 기록 | 상태 조회만으로는 문의 원인과 근거를 연결할 수 없음 |
| 2. [support-agent](../../openspec/changes/archive/2026-10-22-support-agent/proposal.md), Issue#2 | 문의·진단·Trace, 읽기 Tool 5개, Responses Loop | Fake E2E·실제 저장/경합·deadline 테스트. 당시 185 passed 기록과 실제 Smoke 산출물 | Agent 시연만으로 효용을 판단할 수 없어 비교 대상 필요 |
| 3. [workflow-baseline](../../openspec/changes/archive/2026-10-22-workflow-baseline/proposal.md), Issue#3 | 고객 4조회·정책 3검색·LLM 1회, mode 영속화 | 고정 순서·mode 경합·migration·공통 조건 테스트, 실제 Workflow Smoke | 같은 데이터와 출력 계약을 가진 두 실행 방식 확보 |
| 4. [share-diagnosis-runtime](../../openspec/changes/archive/2026-10-22-share-diagnosis-runtime/proposal.md) | Budget·Tool 관찰·LLM 호출을 중립 패키지로 분리 | 기존 Prompt 문자열·옵션·실행 차이 보존 테스트 | 실행 통제 중복에 따른 평가 조건의 이탈 방지 |
| 5. [agent-workflow-evaluation](../../openspec/changes/archive/2026-10-29-agent-workflow-evaluation/proposal.md), Issue#4 | C01~C05·독립 GT·격리 Fixture·반복 Runner·Judge·지표·원본 보존 | 초기 Framework 검증 425 passed 기록. 이후 Calibration v2·Pilot·50회 실측 | 초기 Pilot의 실패 원인과 Judge 신뢰 범위를 더 명확히 할 필요 |
| 6. [improve-llm-execution-reliability](../../openspec/changes/archive/2026-10-29-improve-llm-execution-reliability/proposal.md) | 요청 Timeout 설정·안전한 예외 분류·manifest 적용값 | Fake·설정 우선순위·저장/호환 테스트. 50회 평가는 요청 30초 사용 | 최종 평가에서도 `invalid_output` 세부 원인은 구별 불가 |
| 7. [diagnosis-failure-observability](../../openspec/changes/archive/2026-10-29-diagnosis-failure-observability/proposal.md) | `final_result.failure_metadata`와 실행별 사유 집계 | 사용자 로컬 13 passed / 전체 672 passed 기록 | 향후 출력 거부 경계 분석 가능. 과거 실패 복원·품질 개선은 미입증 |

평가 Change는 Framework 구현 → Pilot → Calibration/출력 계약 보완 → 본 평가 → 실패 관측성 보완 → 결론/Archive에 걸쳐 진행됐다. 5~7을 서로 완전히 분리된 일회성 Phase로 해석하지 않는다.

## SDD가 실제로 사용된 방식

[SDD 운영 가이드](../sdd_workflow.md), [AGENTS.md](../../AGENTS.md), [OpenSpec 설정](../../openspec/config.yaml)은 작은 Change, 검증 가능한 Spec, Application의 권한·실행 통제, Fake 기반 자동 테스트를 일관된 원칙으로 둔다.

| 산출물 | 복원에 사용한 의미 |
|---|---|
| `proposal.md` | 왜 필요한 변경인지, 무엇을 추가·제외했는지 |
| `design.md` | 책임 경계, 흐름, 대안과 Trade-off, 당시 확인한 통합 제약 |
| `specs/<capability>/spec.md` | MUST 요구사항과 WHEN/THEN Scenario. 모든 Change에 같은 단일 `spec.md`가 있는 구조가 아님 |
| `tasks.md` | 구현·검증 단위와 완료 기록. checkbox만으로 실측·운영 반영을 증명하지 않음 |
| [현재 main specs](../../openspec/specs/) | 동기화된 현재 계약. Archive의 과거 계약과 다를 수 있음 |
| [Archive](../../openspec/changes/archive/) | 변경 단위의 문맥 보존. `share-diagnosis-runtime`은 `skip_specs: true`인 내부 변경으로 delta spec이 없음 |

모든 기능이 최초 계획 그대로 끝난 것은 아니다. Seed의 날짜 계약, 엄격한 deadline 대신 실행 Budget을 채택한 결정, 평가 중 출력 Prompt 보완이 실제 이력에서 확인된다. Archive에는 “이번 propose에서는 구현하지 않는다” 같은 작성 시점 문구도 남아 있다. 현재 완료 여부는 코드·테스트·마지막 검증 기록으로 판단했다. 동기화와 Archive는 정리 절차이며 실제 모델 품질이나 기존 DB migration 적용을 대신하지 않는다.

## 근거의 수준과 이번 조사 범위

- **직접 대조:** 현재 Spec·코드·테스트, Git 변경, 로컬 Smoke JSON/보고서, 본 평가 네 JSON의 hash·원시 건수·평균·실패 Trace와 GT/Judge 판정. 본 평가 네 파일의 SHA-256은 [기존 결론](../evaluation/first-evaluation-conclusion.md)의 값과 모두 일치했다.
- **기존 실행 기록:** 69/185/425/672 passed는 각각 연결된 문서에 남은 당시 결과다. 이번 작업에서 pytest·OpenSpec CLI·DB migration·실제 LLM/Judge를 재실행하지 않았다. 테스트 소스 확인은 새 통과 결과가 아니다.
- **해석:** 정형 문의에서 현재 Workflow를 우선하는 선택 등. 관찰 사실과 분리해 표시한다.
- **향후 과제:** 더 넓은 Case, Groundedness·Safety, 새 실행 전략 등. 구현 완료로 쓰지 않는다.

`.evaluation-results/`와 `.smoke-results/`는 Git 제외 경로다. 이 작업 공간에서는 존재를 확인했지만 clone만으로 원본이 전달되지는 않는다. 개별 문서의 해당 상대 링크는 원본을 함께 보존한 환경에서 유효하다. Secret 파일과 `.env`는 열람하지 않았다.

## 문서·코드 차이와 확인할 사항

| 항목 | 조사 결과 / 읽을 때의 기준 |
|---|---|
| Seed | 최초 Backend Archive의 절대 날짜 계약은 과거 상태다. `b0ac208`에서 현재 Spec·코드·테스트가 상대 날짜로 함께 변경됨. [01](01-backend-foundation.md) 참고 |
| Agent 설계 요약 | `docs/support-agent-design.md`는 Agent 당시 구조다. 현재 공통 Runtime·Workflow·새 Trace 필드는 후속 Change와 코드로 보완해야 함 |
| Prompt 불변 범위 | Runtime 분리 Change는 문자열 보존을 요구했지만 `723a9da`에서 `OUTPUT_CONTRACT`를 추가함. 해당 후속 Prompt 변경의 별도 delta spec·선택 근거를 충분히 확인하지 못함. [03](03-workflow-baseline.md), [04](04-evaluation.md) 참고 |
| Calibration 검토자 | Runbook의 `examples_reviewer=null` 설명과 달리 현재 v2 JSON·실제 Calibration snapshot은 값이 있음. 저장된 본 평가 상태는 calibrated임 |
| 검증 기록 시점 | Runbook의 초기 “실측 미실행”, 환경 제약 실패 기록은 당시 기록이다. 후속 본 평가 및 672 passed 기록과 혼동하지 않음 |
| 기존 개발 DB | Migration 003 적용 여부 미확인. 이번 조사에서 DB에 연결하지 않았으며 Archive·테스트 schema 검증으로 적용을 추정하지 않음 |

현재 완료 범위는 진단 Backend, Single Agent, Fixed-step Workflow, 5개 Case 평가와 1차 판단, 이후 실패 관측성까지다. 30개 Case 계획, 독립 Groundedness/Task Success/Safety/Tool Selection Quality, Hybrid Routing, 병렬 Tool 호출, 운영 인증·자동 복구는 완료하지 않았다. 다음 평가의 질문은 “선택적 탐색의 이득이 반복 호출 비용과 원인 누락 위험을 상쇄하는 조건이 있는가”이며, 현재 결과만으로 답하지 않는다.
