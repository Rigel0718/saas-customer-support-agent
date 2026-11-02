# Spec Delta

## ADDED Requirements

### Requirement: Audit Event 저장 구조
시스템은 조직별 Audit Event를 저장해야 한다(MUST). 각 이벤트는 id, organization_id, UTC occurred_at, nullable actor_user_id(null은 system), event_type, nullable target_user_id, JSON payload를 가져야 한다(MUST). event_type은 role_changed, subscription_status_changed, member_invited, user_login, document_viewed, document_edited로 제한해야 한다(MUST). migration은 테이블만 추가하고 기존 데이터를 변경하지 않아야 한다(MUST).

#### Scenario: 허용하지 않은 이벤트 종류
- **WHEN** 허용 목록에 없는 event_type 또는 존재하지 않는 조직을 저장한다
- **THEN** DB 제약으로 저장이 거부된다

#### Scenario: 신규 초기화와 migration
- **WHEN** 빈 DB를 초기화하거나 기존 데이터가 있는 테스트 소유 schema에 004 migration을 적용한다
- **THEN** 같은 audit_events 구조가 생성되고 기존 테이블의 행과 개수는 변하지 않는다

### Requirement: 조직 범위가 강제된 Audit Event 조회
조회 Service는 CallerContext의 조직 이벤트만 반환해야 한다(MUST). 기간·행위자·대상·종류 필터와 limit은 결과를 좁히기만 해야 한다(MUST). 결과는 occurred_at 내림차순·id 오름차순의 items, 필터 조건의 total_count, truncated를 제공해야 한다(MUST). 조회는 데이터를 변경해서는 안 된다(MUST).

#### Scenario: 필터와 잘림
- **WHEN** 같은 조직에 조건을 만족하는 이벤트가 limit보다 많다
- **THEN** 최신순 limit건과 전체 건수, truncated=true를 반환한다

#### Scenario: 다른 조직 식별자
- **WHEN** 다른 조직 사용자의 UUID를 actor 또는 target 필터로 전달한다
- **THEN** 다른 조직 이벤트를 반환하지 않고 빈 items와 total_count=0을 반환한다

### Requirement: Agent Tool get_audit_events
Agent Tool `get_audit_events`는 start_at, end_at, actor_user_id, target_user_id, event_type, limit 인자만 받아야 한다(MUST). 조직 식별자를 인자로 받아서는 안 된다(MUST). limit은 1~50이어야 하고 start_at이 end_at보다 늦으면 안 된다(MUST). 위반은 Service를 실행하지 않고 invalid_arguments 관찰로 반환해야 한다(MUST).

#### Scenario: 정상 조회 관찰
- **WHEN** Agent가 유효한 필터로 Tool을 호출한다
- **THEN** 조직 범위 조회 결과가 tool_result Trace와 다음 모델 입력에 전달된다

#### Scenario: 잘못된 인자
- **WHEN** organization_id 같은 추가 인자, limit=0 또는 51, 역전된 기간을 전달한다
- **THEN** invalid_arguments 관찰을 반환하고 조회를 실행하지 않는다
