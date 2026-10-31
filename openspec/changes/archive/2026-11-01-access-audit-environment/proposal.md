# Proposal

## Why

1차 Evaluation(invite-v1)에서는 Workflow가 Agent보다 정확도·지연·비용 면에서 모두 앞섰다. 다만 당시 환경은 조회 7회로 필요한 정보를 모두 싸게 미리 가져올 수 있었기 때문에, Agent의 선택적 조회가 이득을 낼 여지가 거의 없었다. 2차에서는 **정보량이 커서 미리 다 가져오기 어려운 업무**인 "접근 상실 원인 추적"에서 두 방식이 어떻게 달라지는지 관찰하려 한다.

가설을 미리 확정하지 않는다. 먼저 작은 실험 환경과 파일럿 실행 도구를 만들고, 실제 Trace를 관찰한 뒤 본 평가의 범위를 다시 정한다. 파일럿의 질문은 하나다.

> 노이즈 이벤트가 적을 때와 많을 때, "전부 가져와서 한 번에 판단(Workflow)"과 "필요한 것만 찾아 조회(Agent)"의 정확도·Token·지연은 각각 어떻게 변하는가?

## What Changes

- **Audit Event 데이터:** 조직의 변경 이력을 기록하는 `AuditEvent` 모델과 수동 SQL migration을 추가한다. 이벤트 종류는 다음으로 고정한다.
  - `role_changed`, `subscription_status_changed`, `member_invited`, `user_login`, `document_viewed`, `document_edited`
- **조직 범위 조회 Service:** 조회 대상 조직은 `CallerContext`로만 결정한다. 기간·행위자·대상·종류·개수 필터는 조회 범위를 좁히기만 한다. 결과는 최신순 `items`, 필터에 맞는 전체 건수 `total_count`, 잘림 여부 `truncated`를 반환한다.
- **Agent Tool:** 읽기 전용 Tool `get_audit_events`를 추가한다. 한 번에 최대 50건까지 조회한다.
- **Workflow:** 기존 7단계 조회에 아래 두 단계를 추가해 9단계로 바꾼다. 이후 LLM은 한 번만 호출한다(Upfront Retrieval).
  - 기준 시각 이전 14일의 조직 이벤트 최신 500건
  - `접근` 정책 검색
- **정책 문서:** 멤버 관리 기능의 접근 조건(역할, 구독 상태)과 Audit Event 해석 원칙을 담은 `workspace_access` 정책 문서를 추가한다.
- **파일럿 Fixture와 실행 스크립트:**
  - 접근 상실 Case 4개를 정의한다. 원인은 역할 강등, 구독 정지, 두 원인 복합, 원인 확정 불가다.
  - 결정적 seed로 노이즈 이벤트 N건을 생성한다.
  - 격리된 schema에서 두 모드를 실행하고, 실행별 결과·Trace·Token·Latency를 JSON으로 저장하는 스크립트를 제공한다.

## 제외 범위

- 새 Golden Set 동결, 반복 통계 평가, LLM Judge 채점과 Calibration (파일럿은 사람이 Trace를 직접 검토한다)
- Agent Prompt 튜닝, Fixed-depth Chained Workflow, 인과 체인 깊이 변수, Router·Hybrid
- 기존 invite Dataset·GT·Judge·Evaluation CLI 변경, 이전 평가 결과 재실행
- 실제 계정·구독 변경, Audit Event 쓰기 API

## Capabilities

### New Capabilities

- `audit-events`: Audit Event 저장 구조, 조직 범위 조회, Agent Tool `get_audit_events`.
- `access-pilot`: 접근 상실 Case Fixture, 결정적 노이즈 생성, 두 모드 파일럿 실행 및 결과 저장.

### Modified Capabilities

- `workflow-diagnosis`: 고정 조회를 7단계에서 9단계로 변경한다(Audit Event 선조회, `접근` 정책 검색 추가).
- `support-diagnosis`: Agent 허용 Tool에 `get_audit_events`를 추가한다.
- `policy-search`: `workspace_access` 정책 문서를 추가한다.

## Impact

- **예정 코드:**
  - `app/models/audit.py`, `app/schemas/customer.py`, `app/services/customer.py`
  - `app/diagnosis/tools.py`, `app/agent/tools.py`, `app/workflow/runner.py`, `policies/workspace_access.json`
  - `migrations/004_audit_events.sql`, `evaluation/access_pilot.py`, `scripts/access_pilot.py`, 관련 테스트
- **1차 결과와의 비교 가능성:** 두 모드 모두에 새 정보 원천이 추가된다. invite Case를 다시 실행하면 Agent의 Tool 목록과 Workflow의 조회 단계가 1차와 다르다. 1차 결과는 Commit `723a9da` 기준으로만 해석한다.
- **새로 도입하지 않는 것:** Endpoint, 환경변수, 의존성, 실행 모드
- **실제 OpenAI 호출:** 파일럿 실행은 사용자의 명시적 승인 후 별도로 진행한다. 자동 테스트는 Fake Client만 사용한다.
