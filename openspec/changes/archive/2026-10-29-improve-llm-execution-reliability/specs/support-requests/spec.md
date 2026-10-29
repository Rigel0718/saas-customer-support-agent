# Spec Delta

## MODIFIED Requirements

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

## ADDED Requirements

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
