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
시스템은 GET /support/requests/{id}로 execution_mode를 포함한 문의 표현을, GET /support/requests/{id}/traces로 sequence 오름차순의 items 배열을 반환해야 한다(MUST). 진단은 outcome, summary, causes, next_actions, evidence를 포함해야 한다(MUST). Trace는 sequence, event_type, step, call_id, tool_name, arguments, observation, error_code, duration_ms, created_at 및 LLM 호출의 model/response_id/usage와 nullable llm_metadata를 제공해야 한다(MUST). 해당 없는 필드는 null로 표현해야 한다(MUST).

#### Scenario: 실행 전과 실행 후 Trace
- **WHEN** 실행 전 문의와 두 방식으로 각각 종료된 문의의 Trace를 조회한다
- **THEN** 실행 전에는 빈 items를, 종료 후에는 같은 Trace 필드로 실행 순서를 재구성할 수 있는 기록을 반환한다

#### Scenario: 실행 중 조회
- **WHEN** 동기 실행이 진행 중인 문의를 다른 요청에서 조회한다
- **THEN** running과 확정된 execution_mode 및 null diagnosis를 반환하며 Trace는 종료 저장 전에는 빈 배열일 수 있다

#### Scenario: LLM 메타데이터 조회
- **WHEN** 변경 이후 성공 또는 `llm_error`로 종료된 실행의 LLM Trace를 조회한다
- **THEN** llm_metadata에 실제 적용 요청 Timeout이 있고, `llm_error`이면 안전하게 정규화한 예외 타입·감지된 Timeout 여부·확인 가능한 HTTP Status가 있으며 민감한 예외 원문은 없다

#### Scenario: 해당 없는 이벤트와 이전 Trace
- **WHEN** LLM 호출 이외의 이벤트 또는 메타데이터 도입 전 저장된 Trace를 조회한다
- **THEN** llm_metadata는 null이고 기존 observation·usage·오류 코드·순서·진단 내용은 유지된다

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

### Requirement: LLM 메타데이터의 종료 저장과 데이터 호환
새 LLM 메타데이터는 기존 종료 상태·진단·전체 Trace와 함께 원자적으로 저장해야 한다(MUST). 기존 DB에 새 저장 필드를 추가할 때 이전 행은 null로 유지하고 기존 문의·Trace·관계·진단을 보존해야 한다(MUST). 기존 조직 격리와 DB 실패 계약을 유지하고, 메타데이터를 Diagnosis 근거 관찰로 취급해서는 안 된다(MUST).

#### Scenario: 두 모드의 저장 및 재조회
- **WHEN** Agent와 Workflow가 각각 성공 또는 처리된 LLM 실패 후 종료 저장된다
- **THEN** 별도 세션으로 조회한 메타데이터와 수집값이 같고 기존 completed/failed·diagnosis·error_code 계약이 유지된다

#### Scenario: 종료 저장 실패
- **WHEN** 메타데이터를 포함한 종료 저장이 실패한다
- **THEN** 기존 계약대로 전체 종료 트랜잭션을 rollback하고 503을 반환하며 메타데이터만 부분 저장하지 않는다

#### Scenario: 기존 데이터가 있는 DB 전환
- **WHEN** 이전 문의·진단·Trace가 있는 DB에 새 저장 필드를 추가한다
- **THEN** 이전 레코드 내용·관계·개수는 유지되고 새 필드만 null이며 빈 DB 초기화에서도 동일 저장 구조를 사용할 수 있다

### Requirement: 출력 실패 메타데이터의 Trace 조회
기존 Trace 조회는 nullable `failure_metadata`를 제공해야 한다(MUST). 새 출력 실패의 final_result에는 diagnosis-output-failures 계약의 단계·사유·선택적 필드 경로가 있고 다른 이벤트·정상 결과·이전 기록에는 null이어야 한다(MUST). 기존 llm_metadata·Trace 순서·문의 응답·error_code·HTTP 상태를 유지해야 한다(MUST).

#### Scenario: 두 Mode의 실패 조회
- **WHEN** Agent 또는 Workflow가 invalid_output으로 종료된 뒤 같은 조직에서 Trace를 조회한다
- **THEN** sequence 오름차순 items의 유일한 final_result에 failure_metadata가 있고 execution_failed에는 기존 오류 코드와 null failure_metadata가 있다
- **THEN** 문의 진단/결과 응답은 기존처럼 failed·diagnosis=null·error_code=invalid_output이며 처리된 실패는 200을 반환한다

#### Scenario: 정상 및 이전 기록 조회
- **WHEN** 정상 실행, 출력 실패 final_result가 없는 다른 오류의 실행 또는 새 필드 도입 전의 Trace를 조회한다
- **THEN** failure_metadata는 null이고 기존 진단·관찰·usage·llm_metadata·순서·오류 코드가 유지된다

### Requirement: 출력 실패 메타데이터의 원자적 저장과 권한
출력 실패 metadata는 기존 문의 종료 상태·전체 Trace와 함께 원자적으로 저장하거나 취소해야 한다(MUST). 별도 저장소나 로그로 우회 저장해서는 안 되며 기존 조직 경계·rollback·503·중복 실행 계약을 유지해야 한다(MUST). metadata를 Diagnosis 근거나 모델/Judge 입력으로 사용해서는 안 된다(MUST).

#### Scenario: 저장과 별도 세션 재조회
- **WHEN** 두 Mode의 invalid_output 종료 저장에 성공한다
- **THEN** 별도 세션과 Trace API에서 수집한 metadata가 같고 Diagnosis는 저장되지 않으며 failed 문의 재실행은 409다

#### Scenario: 종료 저장 장애
- **WHEN** metadata를 포함한 종료 저장이 실패한다
- **THEN** 기존 계약대로 전체 종료 트랜잭션을 rollback하고 503을 반환하며 metadata만 부분 저장하지 않는다
- **THEN** 기존 실패 기록 저장도 불가능하면 새 metadata가 유실될 수 있고 추정 정보로 채우지 않는다

#### Scenario: 조직 경계
- **WHEN** 다른 조직의 호출자가 metadata가 포함된 문의의 Trace를 조회하거나 인증 없이 접근한다
- **THEN** 기존과 동일하게 각각 404 또는 401을 반환하며 metadata는 노출되지 않는다

### Requirement: 출력 실패 필드 도입의 데이터 및 Consumer 호환
새 저장 필드는 nullable이며 도입 시 이전 행을 null로 유지하고 기존 문의·진단·Trace·관계·개수를 보존해야 한다(MUST). 새 Reader는 필드 없는 이전 Trace를 읽어야 한다(MUST). Trace API 필드 추가 및 이전 strict Consumer의 새 결과 거부 가능성을 문서에 명시하고 기존 실패를 backfill해서는 안 된다(MUST).

#### Scenario: 기존 DB와 신규 초기화
- **WHEN** 이전 기록이 있는 테스트 소유 schema에 저장 필드를 추가하거나 빈 DB를 초기화한다
- **THEN** 기존 행의 새 필드는 null이고 내용·관계·개수·llm_metadata가 보존되며 신규 초기화도 동일한 저장 구조를 제공한다

#### Scenario: Consumer 호환 범위
- **WHEN** 갱신된 Reader로 새 필드 없는 Trace를 읽거나 새 Trace 형식을 배포한다
- **THEN** 전자는 null로 읽으며 배포 안내는 새 필드를 모르는 strict Consumer의 갱신 필요성과 문의 error_code 계약 불변을 구분한다
