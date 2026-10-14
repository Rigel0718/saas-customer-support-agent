# customer-read-access Specification

## Purpose

인증된 호출자의 조직 범위 안에서 고객 지원 데이터를 읽기 전용으로 조회한다. HTTP API와 향후 Agent·Workflow가 동일한 조회 규칙과 오류 계약을 재사용하고 대상 식별자 조작으로 다른 조직에 접근하지 못하게 한다.

## Requirements

### Requirement: 호출자 인증 컨텍스트
Application은 개발 전용 환경 설정의 불투명 Bearer 토큰을 사용자에 매핑하고 DB에서 호출자의 user_id, organization_id, role을 확인해야 한다(MUST). 요청의 대상 ID나 조직 헤더로 호출자 범위를 설정해서는 안 된다(MUST). 토큰 누락·오류·삭제된 사용자 매핑은 401이며 토큰 설정 없이 시작할 때 인증을 우회해서는 안 된다(MUST).

#### Scenario: 인증 실패
- **WHEN** 토큰이 없거나 유효하지 않거나 매핑된 사용자가 존재하지 않는다
- **THEN** 조회 API는 401을 반환하고 고객 데이터를 반환하지 않는다

#### Scenario: 조직 범위 위조
- **WHEN** 유효한 토큰과 다른 조직의 대상 ID 또는 조직 헤더를 함께 전송한다
- **THEN** 호출자 조직은 DB에서 확인한 조직으로 유지된다

### Requirement: 조직 경계의 공통 강제
읽기 Service는 신뢰된 호출자 컨텍스트를 필수로 받고 모든 조회에서 호출자의 조직 범위를 강제해야 한다(MUST). 같은 조직의 owner/admin/member는 이번 Change의 모든 조회를 수행할 수 있으며 다른 조직의 사용자·조직·구독·결제는 반환하지 않아야 한다(MUST). 다른 조직의 대상과 존재하지 않는 대상은 같은 not_found 오류로 처리해야 한다(MUST).

#### Scenario: HTTP 교차 조직 접근
- **WHEN** 조직 A의 호출자가 조직 B의 사용자·조직·구독·결제 이력 URL을 각각 조회한다
- **THEN** 각 요청은 동일한 404 not_found를 반환하며 B의 존재 여부와 데이터가 노출되지 않는다

#### Scenario: Service 직접 호출
- **WHEN** A의 신뢰된 컨텍스트로 Service에 B의 대상 ID를 직접 전달한다
- **THEN** HTTP Handler를 거치지 않아도 같은 not_found 오류가 발생하고 데이터가 반환되지 않는다

#### Scenario: 컨텍스트 없는 조회
- **WHEN** 신뢰된 호출자 컨텍스트 없이 Service 조회를 시도한다
- **THEN** 조회가 거부되고 고객 데이터가 반환되지 않는다

### Requirement: 읽기 API 계약
시스템은 GET /users/{user_id}, /organizations/{organization_id}, /organizations/{organization_id}/subscription, /organizations/{organization_id}/payments를 제공해야 한다(MUST). 유효한 UUID 대상 조회는 200 JSON을 반환하며 필드는 customer-data 명세를 따라야 한다(MUST). 결제 응답은 items 배열이며 모든 이력을 created_at 내림차순, 같은 시각은 id 오름차순으로 반환해야 한다(MUST).

#### Scenario: 동일 조직의 조회 성공
- **WHEN** 각 역할의 유효한 호출자가 같은 조직의 존재하는 대상에 네 API를 호출한다
- **THEN** 사용자 역할, 조직 팀원 수·제한, 저장된 구독 플랜·상태·만료일, 결제 상태·결제 시점이 반환된다

#### Scenario: 결제 순서와 부재
- **WHEN** 결제 이력이 여러 건이거나 없는 자기 조직을 조회한다
- **THEN** 고정 정렬의 items 또는 빈 items가 200으로 반환된다

#### Scenario: 구독 부재
- **WHEN** 자기 조직은 존재하지만 현재 Subscription이 없다
- **THEN** 구독 API는 404 not_found를 반환한다

### Requirement: 입력 및 의존성 오류 계약
시스템은 잘못된 UUID에 422를, 존재하지 않는 조회 대상에 404 not_found를 반환해야 한다(MUST). DB 연결·조회 불가 시 503 service_unavailable을 반환하며 401/404/503은 detail.code와 detail.message를 가진 JSON이어야 한다(MUST). 422는 기본 입력 검증 형식을 사용하고 오류 응답에 토큰·접속 문자열·SQL·스택을 노출하지 않아야 한다(MUST).

#### Scenario: 잘못된 식별자와 없는 대상
- **WHEN** 인증된 호출자가 UUID가 아닌 ID 또는 존재하지 않는 UUID를 전달한다
- **THEN** 각각 422와 404 not_found를 반환한다

#### Scenario: DB 접근 실패
- **WHEN** 조회에 필요한 DB 접근이 실패한다
- **THEN** 503 service_unavailable을 반환하고 내부 연결 정보는 응답에 포함되지 않는다

### Requirement: 조회의 읽기 전용성
조회 API와 공통 Service는 데이터 생성·수정·삭제·진단 상태 계산을 수행하지 않아야 한다(MUST). 고객 도메인에 POST/PUT/PATCH/DELETE API를 제공하지 않아야 한다(MUST). Seed와 테스트 초기화는 별도 관리 작업으로 한정해야 한다(MUST).

#### Scenario: 조회 전후 데이터 동일
- **WHEN** 정상·오류 조회와 Service 직접 호출을 실행한다
- **THEN** 네 도메인의 저장 값과 레코드 개수가 조회 전과 같다

#### Scenario: 변경 요청 차단
- **WHEN** 고객 조회 경로에 POST/PUT/PATCH/DELETE를 요청한다
- **THEN** 405를 반환하며 데이터가 변경되지 않는다
