# customer-data Specification

## Purpose

고객 지원에서 공통으로 사용할 사용자·조직·구독·결제 데이터를 일관된 관계로 제공한다. 정상 및 문제 상태를 재현 가능한 데이터로 구성하여 후속 조회 테스트와 진단 기능의 기반을 마련한다.

## Requirements

### Requirement: 핵심 도메인 관계 및 조회 필드
시스템은 User의 id, organization_id, role; Organization의 id, name, member_count, member_limit; Subscription의 id, organization_id, plan, status, expires_at; Payment의 id, organization_id, status, paid_at을 제공해야 한다(MUST). User와 Payment는 하나의 조직에 속하고 조직에는 여러 User와 Payment 및 최대 하나의 현재 Subscription이 존재해야 한다(MUST).

#### Scenario: 조직 데이터 관계 조회
- **WHEN** 여러 사용자와 결제 이력 및 구독을 가진 조직을 조회한다
- **THEN** 모든 데이터의 조직 관계가 일치하고 member_count는 해당 조직의 User 수와 같으며 주요 필드가 반환된다

#### Scenario: 구독 및 결제 부재
- **WHEN** 구독과 결제 이력이 없는 조직을 조회한다
- **THEN** 조직과 사용자는 유효하게 존재하며 구독 부재와 빈 결제 이력을 표현할 수 있다

### Requirement: 데이터 무결성
시스템은 존재하지 않는 조직 참조, 조직별 중복 Subscription, 음수 member_limit, 허용하지 않은 role·plan·status를 저장하지 못하도록 해야 한다(MUST). role은 owner/admin/member, plan은 free/pro/enterprise, 구독 status는 active/past_due/expired/canceled, 결제 status는 succeeded/failed/pending으로 제한하며 succeeded에만 paid_at이 있어야 한다(MUST).

#### Scenario: 잘못된 데이터 저장 거부
- **WHEN** 잘못된 외래 키, 중복 구독, 음수 제한, 알 수 없는 열거값 또는 결제 상태와 paid_at의 불일치를 저장하려 한다
- **THEN** 각 데이터는 저장이 거부되고 유효 데이터의 관계가 유지된다

#### Scenario: 업무 오류 상태 허용
- **WHEN** 만료일이 지난 구독 또는 실패 결제를 저장한다
- **THEN** 무결성 제약을 충족하는 업무 오류 상태는 정상적으로 저장된다

### Requirement: 재현 가능한 Seed
시스템은 고정 ID와 기준 UTC 시각에 대한 상대 날짜를 사용하는 Seed 실행 기능을 제공해야 한다(MUST). 기본 기준 시각은 Seed 실행 당시 UTC이며, 자동 테스트에서는 기준 시각을 주입할 수 있어야 한다(MUST). 전용 개발·테스트 DB에서 같은 기준 시각으로 Seed를 두 번 적용하면 값·개수·관계가 동일해야 하며 다른 데이터를 삭제해서는 안 된다(MUST). 다른 기준 시각으로 재실행하면 동일한 고정 ID의 날짜만 상대 간격에 맞춰 갱신하고 시나리오의 상태와 관계를 유지해야 한다(MUST).

#### Scenario: Seed 재실행
- **WHEN** 빈 전용 DB에 같은 기준 시각으로 Seed를 적용하고 다시 적용한다
- **THEN** 두 실행 후 도메인 레코드의 값과 개수는 같고 중복이 없으며 Seed 소유가 아닌 기존 데이터는 유지된다

### Requirement: 정상 및 문제 상태 fixture
Seed는 정상 활성 구독·성공 결제, 만료 구독, past_due 구독·실패 결제, member 역할, 팀원 수가 제한과 같은 조직, 구독·결제 부재, pending 결제를 포함해야 한다(MUST). 최소 두 조직으로 교차 접근 검증이 가능해야 하고 과거 결제 여러 건과 같은 결제 시각의 레코드도 포함해야 한다(MUST).

#### Scenario: 고정 상태 검증
- **WHEN** Seed를 조회한다
- **THEN** 정상·문제·부재 상태와 owner/admin/member가 고정 ID로 식별되며 member 역할과 제한 도달 상태를 구분할 수 있다

#### Scenario: 시간 재현
- **WHEN** 서로 다른 기준 UTC 시각으로 같은 Seed를 적용한다
- **THEN** 활성 구독의 만료일은 각 기준 시각의 30일 후, 만료 구독의 만료일은 50일 전, 과거 성공 결제는 30일 전이며 상태와 고정 ID는 유지된다

#### Scenario: 기준 시각 주입
- **WHEN** 자동 테스트에서 같은 기준 UTC 시각을 주입해 Seed를 생성한다
- **THEN** 실제 실행 시각과 관계없이 동일한 상태와 날짜를 재현할 수 있다
