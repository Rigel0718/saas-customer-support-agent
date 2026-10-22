# Proposal

## Why

프로젝트의 핵심 목표는 Agent와 Workflow의 Trade-off를 실증적으로 평가하고, 어떤 문제와 조건에서 어떤 아키텍처가 유리한지 판단할 Evaluation 체계를 구축하는 것이다. Support Agent 구현을 마친 현재, 이번 Change는 Fixed-step Workflow를 첫 기준선으로 추가한다. 이후 Evaluation Framework와 Trade-off Analysis를 수행하고, 그 결과에 따라 Case-specific Workflow와 Hybrid Routing을 후속 확장으로 고려한다. Hybrid 구조는 사전에 확정한 정답도 이번 구현 대상도 아니다.

비교 질문은 진단 품질·근거의 타당성, 정책 준수·안전성, LLM 호출 수·Token 사용량, Tool 호출 수·불필요한 조회, 실행 지연, 실패율·실패 유형에서 두 시스템이 어떤 Trade-off를 보이는가이다. 두 방식은 Tool 선택 외에도 모델 호출 수, Context 구성, 조회 범위가 다르므로 결과를 동적 Tool 선택 단일 요소의 인과적 효과로 단정하지 않는다. 이 고정 전체 조회 Workflow는 여러 가능한 Workflow 중 하나이며, 평가 결과는 이 Baseline과 현재 Agent 구현 사이의 비교로 한정한다.

## What Changes

- 기존 동기 진단 API에 `mode=agent|workflow` 선택을 추가한다. 생략 시 기존 Agent 동작을 유지한다.
- Application이 문의 생성자·조직·구독·결제를 정해진 순서로 조회하고 고정 검색어로 정책을 검색한 뒤, LLM을 최대 한 번 호출하는 Workflow 진단을 추가한다. 부재/빈 결과는 관찰로 전달하고 기술 오류는 중단한다.
- 기존 고객/정책 Service, 인증·조직 격리, 구조화된 진단·근거 검증, 상태 전이·원자적 저장 계약을 재사용한다.
- 실행 방식을 저장·조회하고 기존 Trace로 조회 순서, 관찰, LLM 사용량·시간, 종료 사유를 확인한다.
- Fake Responses와 고정 데이터·기준 시각으로 성공·실패·권한 경계 및 Agent 회귀를 검증한다.
- 제외: Evaluation 데이터셋·반복 실행·채점·통계 처리·데이터 동결·가격 환산·비교 결과, Metrics DB·집계 테이블, 실제 API 검증, Agent 전략/Prompt 개선, Workflow Engine, Case-specific Workflow·Router·Hybrid Orchestration, 문의 재실행·복수 실행 모델, 상태 변경 Tool.

## Capabilities

### New Capabilities

- `workflow-diagnosis`: Application이 조회 순서를 통제하는 읽기 전용 Workflow와 단일 LLM 진단, 실행 한도·Trace·자동 검증.

### Modified Capabilities

- `support-requests`: 실행 방식 선택·저장·조회, 방식 간 중복 실행 방지 및 기존 오류/저장 계약의 Workflow 적용.

## Impact

- `app/services/support.py`, `app/api/support.py`, 지원 DTO/모델에 필요한 최소 확장과 별도의 Workflow 실행 함수 추가를 예상한다. 고객/정책 Service와 Agent의 동적 Tool 선택 로직은 유지한다.
- 실행 방식 컬럼의 기존 DB 반영 절차가 필요하다. 기존 데이터의 Agent 실행 여부를 보존한다. 신규 외부 의존성은 추가하지 않는다.
- 기존 API 호출은 계속 Agent로 실행된다. 문의 응답에 nullable `execution_mode`가 추가되므로 정확한 필드 집합을 검사하는 테스트/클라이언트는 조정이 필요하다.
- 같은 문의는 여전히 한 번만 실행한다. 두 방식을 비교할 때 동일 생성자·내용의 문의를 각각 생성한다. 이번에는 Fake Client·고정 Fixture·시계 주입으로 공통 입력·권한·데이터·정책·모델 조건과 비교 가능한 기준 시각을 검증할 기반을 확보하며, 실제 평가의 데이터 동결과 실행 관리는 Evaluation Change가 담당한다.
- 이번 작업은 기존 계획 산출물의 검토·수정으로 한정하며 구현을 시작하지 않는다.
