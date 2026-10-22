# support-requests Specification

## Purpose

인증된 고객 조직의 문의를 접수하고 동기 진단을 실행하여 상태·결과·근거·Trace를 조회할 수 있게 한다. 문의의 실행 상태와 저장 결과를 일관되게 관리하고 다른 조직의 접근 및 중복 실행을 차단한다.

## Requirements

### Requirement: 문의 접수 계약
시스템은 POST /support/requests에서 content만 받아 문의를 생성하고 201을 반환해야 한다(MUST). content는 앞뒤 공백 제거 후 1~4000자이며 추가 body 필드는 거부해야 한다(MUST). id, requester_user_id, organization_id, content, status, created_at, started_at, finished_at, diagnosis, error_code를 반환하고 최초 상태는 pending, 진단·오류·실행 시각은 null이어야 한다(MUST). 호출자와 조직은 인증 정보로 결정해야 한다(MUST).

#### Scenario: 문의 생성
- **WHEN** 유효한 호출자가 문의 내용을 전송한다
- **THEN** UUID 문의가 저장되고 생성자와 조직은 인증된 값이며 201 응답 이후 별도 조회에서 pending 상태가 확인된다

#### Scenario: 입력 오류와 범위 위조
- **WHEN** 인증된 호출자가 빈 내용, 공백만 있는 내용, 4000자 초과 내용 또는 organization_id/user_id/role 등의 추가 필드를 전송한다
- **THEN** 422를 반환하고 문의나 실행 기록을 저장하지 않는다

### Requirement: 동기 진단 및 상태 전이
시스템은 POST /support/requests/{id}/diagnose로 pending 문의를 선택한 방식으로 동기 실행하여 running에서 completed 또는 failed로 전이해야 한다(MUST). 완료 시각은 UTC이며 종료 결과가 저장된 뒤 200으로 문의 표현을 반환해야 한다(MUST). 동일 문의는 방식에 관계없이 한 번 실행하며 running/failed 재호출은 409 conflict여야 한다(MUST). completed 재호출은 저장된 방식과 같으면 저장 결과를 200으로 반환하고 다르면 409 conflict여야 한다(MUST).

#### Scenario: 진단 완료와 재조회
- **WHEN** pending 문의의 진단이 정상 종료된다
- **THEN** completed 상태와 진단이 저장되고 실행 응답 및 GET 응답의 결과가 일치한다

#### Scenario: 중복 및 동시 실행
- **WHEN** 동일 pending 문의에 같거나 다른 방식의 두 실행 요청이 동시에 도착하거나 running 문의에 재요청한다
- **THEN** 한 실행만 조회·LLM 호출을 수행하고 다른 요청은 409이며 동일 방식의 completed 재요청은 추가 조회·LLM 호출·Trace 생성 없이 기존 결과를 반환한다

#### Scenario: 처리된 Agent 실패
- **WHEN** Agent의 실행 한도 초과, LLM 오류 또는 유효하지 않은 최종 출력으로 실행이 실패하고 DB 저장은 가능하다
- **THEN** failed 상태, finished_at, 안전한 error_code 및 수집한 Trace가 저장되고 200으로 반환되며 diagnosis는 null이고 재실행은 409다

#### Scenario: 처리된 Workflow 실패
- **WHEN** Workflow의 실행 시간 초과, LLM/조회 오류 또는 유효하지 않은 최종 출력으로 실행이 실패하고 DB 저장은 가능하다
- **THEN** 동일하게 failed 상태와 실행 방식·시각·오류·수집 Trace를 저장하고 diagnosis=null의 문의를 200으로 반환하며 두 방식 모두 재실행은 409다

#### Scenario: 완료 후 다른 방식 요청
- **WHEN** workflow로 완료한 문의에 mode 생략/agent 요청을 하거나 agent로 완료한 문의에 workflow 요청을 한다
- **THEN** 409를 반환하고 저장된 방식·진단·Trace를 유지하며 추가 실행하지 않는다

### Requirement: 문의 결과 및 Trace 조회
시스템은 GET /support/requests/{id}로 execution_mode를 포함한 문의 표현을, GET /support/requests/{id}/traces로 sequence 오름차순의 items 배열을 반환해야 한다(MUST). 진단은 outcome, summary, causes, next_actions, evidence를 포함해야 한다(MUST). Trace는 sequence, event_type, step, call_id, tool_name, arguments, observation, error_code, duration_ms, created_at 및 LLM 호출의 model/response_id/usage를 제공해야 한다(MUST). 해당 없는 필드는 null로 표현해야 한다(MUST).

#### Scenario: 실행 전과 실행 후 Trace
- **WHEN** 실행 전 문의와 두 방식으로 각각 종료된 문의의 Trace를 조회한다
- **THEN** 실행 전에는 빈 items를, 종료 후에는 같은 Trace 필드로 실행 순서를 재구성할 수 있는 기록을 반환한다

#### Scenario: 실행 중 조회
- **WHEN** 동기 실행이 진행 중인 문의를 다른 요청에서 조회한다
- **THEN** running과 확정된 execution_mode 및 null diagnosis를 반환하며 Trace는 종료 저장 전에는 빈 배열일 수 있다

### Requirement: 지원 데이터의 조직 격리
문의 생성·두 방식의 실행·결과·Trace API는 기존 Bearer 인증을 사용하고 동일 조직의 owner/admin/member에게 같은 접근을 제공해야 한다(MUST). 다른 조직의 문의와 존재하지 않는 문의는 같은 404 not_found로 처리해야 한다(MUST). 인증 실패는 401이며 요청 body, 조직 헤더, 문의 본문 또는 LLM 식별자가 인증 범위를 변경해서는 안 된다(MUST).

#### Scenario: 동일 조직 접근
- **WHEN** owner가 생성한 문의를 같은 조직의 admin/member가 어느 방식으로 실행하거나 조회한다
- **THEN** 조직 범위 안에서 동일하게 허용하고 문의 생성자 정보는 원래 값으로 유지한다

#### Scenario: 교차 조직 접근 및 인증 실패
- **WHEN** 다른 조직의 문의에 두 방식의 실행·결과·Trace 요청을 하거나 인증 없이 지원 API에 요청한다
- **THEN** 각각 동일한 404 또는 401을 반환하고 조회·LLM 호출 또는 저장 데이터 변경을 수행하지 않는다

### Requirement: 저장 모델 무결성과 초기화
문의는 생성자·조직 참조, 내용, 상태 및 UTC 시각을 저장해야 한다(MUST). 진단은 문의별 최대 하나이며 Trace는 문의별 고유 양의 sequence를 가져야 한다(MUST). 잘못된 참조·상태·결과 열거값과 중복 진단/sequence는 저장할 수 없어야 한다(MUST). DB 초기화와 테스트 초기화 모두 기존 고객 테이블 및 새 지원 테이블을 등록해야 한다(MUST).

#### Scenario: 잘못된 저장 거부
- **WHEN** 없는 문의의 진단/Trace, 중복 진단/sequence, 허용되지 않은 상태/outcome 또는 없는 사용자/조직 참조를 저장한다
- **THEN** 저장이 거부되고 유효한 기존 데이터가 유지된다

#### Scenario: 빈 DB 초기화
- **WHEN** API Router를 먼저 로드하지 않고 DB 초기화 또는 테스트 초기화를 수행한다
- **THEN** 네 고객 테이블과 문의·진단·Trace 테이블이 모두 생성되고 관계가 유효하다

### Requirement: 저장의 원자성과 오류 계약
문의 생성과 실행 시작 상태·실행 방식은 각각 확정 저장해야 하고 두 방식의 종료 상태·진단·전체 Trace는 함께 저장하거나 함께 취소해야 한다(MUST). Application은 실패한 저장을 rollback해야 한다(MUST). DB 실패는 503 service_unavailable이며 성공 저장으로 보고해서는 안 된다(MUST). 기존 401/404/503과 신규 409는 detail.code/message, 잘못된 UUID는 422를 반환하고 내부 정보는 노출하지 않아야 한다(MUST).

#### Scenario: 종료 저장 실패
- **WHEN** 어느 방식이든 진단 종료 저장 도중 오류가 발생한다
- **THEN** 해당 종료 트랜잭션의 상태·진단·Trace가 모두 rollback되고 API는 503을 반환하며 부분 진단은 조회되지 않는다

#### Scenario: 실패 기록 보존의 한계
- **WHEN** 종료 저장 실패 후 실패 기록 저장이 가능하거나 DB 장애가 지속된다
- **THEN** 가능한 경우 새 트랜잭션으로 failed와 안전한 오류·수집 Trace를 함께 저장하고, 지속 장애 시 이미 확정한 문의/running 및 실행 방식만 남을 수 있으며 두 경우 모두 원래 요청은 503이다

#### Scenario: 문의 생성 저장 실패
- **WHEN** 문의 생성 commit이 실패한다
- **THEN** 503을 반환하고 rollback하며 201 또는 문의 생성 성공으로 보고하지 않는다

#### Scenario: 실행 시작 저장 실패
- **WHEN** running과 execution_mode를 확정하는 트랜잭션이 실패한다
- **THEN** 해당 변경을 rollback하고 503을 반환하며 Workflow 조회나 Agent/Workflow LLM 호출을 시작하지 않는다

### Requirement: 진단 방식 선택과 기록
진단 API는 query parameter mode에 agent 또는 workflow를 허용하고 생략 시 agent를 선택해야 한다(MUST). 유효하지 않은 값은 422이며 실행하지 않아야 한다(MUST). 문의 생성 body는 content만 허용해야 한다(MUST). 문의 표현에 nullable execution_mode를 추가하고 pending은 null, 실행 시작 이후는 선택한 방식을 저장·반환해야 한다(MUST). 방식은 running 전이와 함께 원자적으로 확정해야 한다(MUST).

#### Scenario: 기존 호출과 Workflow 선택
- **WHEN** pending 문의를 mode 없이 또는 mode=workflow로 진단한다
- **THEN** 각각 agent 또는 workflow를 실행하며 실행 응답 및 후속 GET의 execution_mode가 일치한다

#### Scenario: 입력 검증과 실행 전 상태
- **WHEN** 문의를 생성·조회하거나 진단에 허용되지 않은 mode를 지정한다
- **THEN** 실행 전 execution_mode는 null이고 잘못된 mode는 422이며 pending 상태·실행 방식·Trace는 변경되지 않는다

#### Scenario: 기존 DB 데이터 호환
- **WHEN** 변경 전 pending/running/completed/failed 문의가 있는 DB에 변경을 적용한다
- **THEN** pending의 execution_mode는 null이고 기존 실행 상태 세 종류는 agent로 보존되며 기존 결과·Trace·관계가 유지된다
