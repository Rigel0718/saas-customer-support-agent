# Spec Delta

## ADDED Requirements

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
