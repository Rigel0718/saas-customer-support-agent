# Spec Delta

## ADDED Requirements

### Requirement: 접근 상실 파일럿 Case
파일럿은 같은 문의 내용을 쓰는 네 Case를 제공해야 한다(MUST). A01은 관리자에 의한 요청자 역할 강등, A02는 결제 실패 후 system의 구독 past_due 전환, A03은 두 원인의 복합, A04는 관련 변경이 없고 5일 전 승격 이벤트만 있는 원인 확정 불가 상황이다(MUST). A04의 승격 이벤트는 Workflow Audit 선조회 기간(14일) 안에 있어야 한다(MUST). Case 데이터의 현재 상태(역할·구독·결제)는 원인 이벤트와 모순되지 않아야 한다(MUST).

#### Scenario: Case 상태의 일관성
- **WHEN** 각 Case Fixture를 생성한다
- **THEN** A01·A03의 요청자 role은 member이고 A02·A03의 구독은 past_due이며 원인 이벤트의 to_role/to_status와 일치한다

### Requirement: 결정적 노이즈 생성
노이즈 생성은 (seed, N, case)가 같으면 같은 이벤트 id·시각·종류·행위자·대상을 생성해야 한다(MUST). 노이즈는 기준 시각 이전 30일 범위에 분포하고 요청자 이외 사용자의 role_changed를 포함해야 한다(MUST). 노이즈는 요청자의 역할이나 조직 구독 상태를 바꾸는 이벤트를 포함해서는 안 된다(MUST).

#### Scenario: 재현성
- **WHEN** 같은 seed·N·case로 두 번 생성한다
- **THEN** 두 결과가 동일하고 seed가 다르면 결과가 달라진다

#### Scenario: 원인 이벤트 보존
- **WHEN** N=5000 노이즈를 생성한다
- **THEN** Case의 원인 이벤트는 그대로 유지되고 요청자를 대상으로 하는 role_changed 및 subscription_status_changed 노이즈는 없다

### Requirement: 격리된 파일럿 실행과 결과 저장
파일럿 스크립트는 기존 Evaluation DB 검증을 통과한 평가 DB의 새 schema에서만 실행해야 한다(MUST). 각 실행은 새 문의로 기존 diagnose 계약을 호출해야 한다(MUST). 결과는 `access-<실행 수>-v<버전>` 디렉터리에 manifest(실행 조건·plan·출처), executions(case·N·mode·요청 결과·LLM/Tool calls·tokens·latency·Workflow audit 관찰의 returned/total_count/truncated·전체 Trace), summary(모드·모드×N·Case×모드×N 집계) JSON으로 나눠 저장해야 한다(MUST). 기존 디렉터리를 덮어쓰지 않고 다음 버전 번호를 사용해야 한다(MUST). 채점이나 정답 판정을 하지 않아야 한다(MUST).

#### Scenario: Fake Client 파일럿
- **WHEN** Fake Client로 두 Case·두 N·두 모드를 실행한다
- **THEN** 8건의 실행 결과가 저장되고 모드 순서는 반복마다 교대하며 실제 API를 호출하지 않는다

#### Scenario: 잘못된 DB 선택
- **WHEN** 평가 DB URL이 개발 또는 테스트 DB와 같다
- **THEN** schema를 만들지 않고 실행을 거부한다

### Requirement: 실행 전 동결하는 접근 상실 Ground Truth
파일럿은 실행 결과와 분리된 `access_gt.json`을 제공해야 한다(MUST). GT는 Case별 expected_outcome, primary_causes, excluded_causes, required_facts, policy_document_ids와 공통 labeling_rules를 가져야 한다(MUST). 원인은 고정 어휘로 제한하고, primary와 excluded는 겹치지 않으며, 원인이 있으면 diagnosed·없으면 needs_information이어야 한다(MUST). 실행 시 GT를 검증하고 SHA-256과 내용을 manifest에 기록해야 한다(MUST). 실행 결과를 본 뒤 GT를 수정하면 버전을 올려야 한다(MUST).

#### Scenario: GT와 Fixture의 일치
- **WHEN** GT를 불러와 Case 정의와 대조한다
- **THEN** 각 Case의 primary_causes는 Fixture의 역할 강등·구독 정지 여부와 일치하고 A04는 과거 승격을 제외 원인으로 둔다

#### Scenario: 일관되지 않은 GT
- **WHEN** 원인이 겹치거나 어휘 밖 원인, outcome 불일치, Case 누락, 없는 정책 ID, 빈 labeling_rules를 가진 GT를 불러온다
- **THEN** invalid_ground_truth로 거부한다
