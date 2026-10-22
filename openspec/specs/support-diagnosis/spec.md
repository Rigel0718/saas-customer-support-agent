# support-diagnosis Specification

## Purpose

고객 문의에 필요한 데이터와 정책을 읽기 전용 Tool로 조회하고 관찰 결과에 따라 후속 행동을 선택하는 Single Agent 진단을 제공한다. 실행은 Application의 권한·종료 통제 아래 수행하며 최종 판단의 근거와 실행 기록을 확인할 수 있게 한다.

## Requirements

### Requirement: 관찰에 따른 Single Agent 실행
시스템은 OpenAI Responses API 기반의 Single Agent로 문의를 진단해야 한다(MUST). 모델이 선택한 허용 Tool의 결과를 동일 실행의 다음 모델 입력으로 전달하여 추가 조회 또는 최종 판단을 선택하게 해야 한다(MUST). 고객 조회 순서를 고정 Workflow로 대신해서는 안 되며 Tool 호출과 결과는 call_id로 대응되어야 한다(MUST).

#### Scenario: 복합 문의의 후속 조회
- **WHEN** 구독 조회 후 모델이 결제 상태 추가 확인을 선택하고 이후 정책을 조회한다
- **THEN** 각 관찰 결과가 다음 모델 호출에 포함되고 최종 진단은 그 실행의 결제·구독·정책 근거와 연결된다

#### Scenario: 여러 Tool 요청 처리
- **WHEN** 하나의 모델 응답에 여러 Tool 호출이 반환된다
- **THEN** 각 호출을 반환 순서대로 처리하고 call_id별 결과를 다음 입력에 포함하며 호출 수 한도를 개별 적용한다

### Requirement: 허용 Tool과 입력 검증
Agent는 get_user(user_id), get_organization(organization_id), get_subscription(organization_id), get_payment_history(organization_id), search_policy(query)만 사용할 수 있어야 한다(MUST). 식별자는 UUID, 검색어는 공백 제거 후 1~200자이고 추가 인자는 거부해야 한다(MUST). 등록되지 않은 Tool, 잘못된 JSON·타입·필수 인자는 실행하지 않고 unknown_tool 또는 invalid_arguments 관찰값으로 반환해야 한다(MUST).

#### Scenario: 정상 Tool 관찰
- **WHEN** 같은 조직의 유효한 대상으로 네 고객 Tool을 호출한다
- **THEN** 기존 조회 계약과 동일한 필드·정렬의 데이터를 반환하고 네 고객 도메인 레코드는 변경되지 않는다

#### Scenario: 잘못된 Tool 호출
- **WHEN** 등록되지 않은 환불 Tool, 잘못된 UUID/JSON 또는 role·context 등의 추가 인자를 모델이 전달한다
- **THEN** Service를 실행하지 않고 안전한 오류 관찰 및 Trace를 남겨 다음 판단에 전달한다

### Requirement: 인증 경계와 진단 대상
Application은 HTTP 인증으로 생성한 CallerContext를 실행과 Tool에 주입해야 하며 LLM이 컨텍스트·Session·권한을 생성하거나 교체할 수 없어야 한다(MUST). 문의 생성자를 기본 진단 대상 사용자로 제공하고 실행 호출자의 인증된 조직 범위로 모든 조회를 통제해야 한다(MUST). 동일 조직의 다른 사용자는 기존 접근 규칙으로 조회할 수 있어야 한다(MUST).

#### Scenario: LLM의 교차 조직 조회
- **WHEN** 문의에 포함된 공격 지시 또는 모델 선택으로 다른 조직의 사용자·조직·구독·결제 ID를 전달한다
- **THEN** 없는 대상과 동일한 not_found 관찰만 반환하고 해당 조직 데이터가 LLM 입력·Trace·최종 근거에 포함되지 않는다

#### Scenario: 동일 조직의 다른 실행자
- **WHEN** 같은 조직의 admin이 member가 생성한 초대 문의를 진단한다
- **THEN** 기본 대상은 생성자인 member로 유지되며 admin을 문의 당사자로 오인하여 권한을 판단하지 않는다

### Requirement: 조회 부재와 Tool 오류의 구분
not_found는 안전한 오류 관찰, 빈 결제·정책 결과는 정상 빈 결과로 모델에 전달해야 한다(MUST). 만료·past_due·실패 결제는 업무 상태로 전달해야 한다(MUST). 컨텍스트 오류, DB 실패, 예상하지 못한 Tool 실행 오류는 실행을 중단해야 하며 모델이 이를 정상 고객 데이터로 사용해서는 안 된다(MUST).

#### Scenario: 조회 부재 후 판단
- **WHEN** 구독이 없거나 결제 이력이 비어 있거나 정책 검색 결과가 없다
- **THEN** 모델은 해당 관찰을 받고 추가 조회, 정보 요청 또는 이관을 선택할 수 있으며 부재만으로 실행 실패가 되지 않는다

#### Scenario: 치명적 Tool 오류
- **WHEN** 고객 Tool의 DB 접근 또는 내부 실행이 실패한다
- **THEN** 추가 LLM/Tool 호출을 중단하고 안전한 실패 코드를 기록하며 DB 오류의 HTTP·저장 처리는 support-requests 계약을 따른다

### Requirement: 구조화된 진단과 근거 연결
최종 진단은 outcome(diagnosed/needs_information/escalate), 비어 있지 않은 summary, causes 배열, next_actions 배열, evidence 배열을 포함해야 한다(MUST). evidence 항목은 trace_sequence, nullable policy_document_id, statement를 포함하고 같은 실행의 실제 관찰을 참조해야 한다(MUST). diagnosed는 원인과 최소 한 근거가 있어야 하며 정보 요청·이관은 필요한 질문/안내를 포함해야 한다(MUST). 참조 유효성은 Application이 검증해야 한다(MUST).

#### Scenario: 근거가 있는 진단
- **WHEN** 모델이 사용자 역할·구독·결제·정책 관찰을 근거로 구조화된 결과를 반환한다
- **THEN** 관찰 Trace와 검색에서 실제 반환된 정책 ID만 근거로 저장하며 결과는 completed가 된다

#### Scenario: 정보 부족 또는 해결 불가
- **WHEN** 관찰만으로 원인을 확정할 수 없거나 지원 범위를 벗어난 문의다
- **THEN** needs_information 또는 escalate로 종료하고 필요한 질문/이관 안내를 저장하며 실행 자체는 completed다

#### Scenario: 유효하지 않은 최종 결과
- **WHEN** 모델이 잘못된 형식, 비어 있는 출력, 존재하지 않는 Trace/정책 참조 또는 근거 없는 diagnosed를 반환한다
- **THEN** completed로 저장하지 않고 invalid_output으로 failed 처리하며 검증 실패 Trace를 남긴다

### Requirement: 종료 조건과 실행 한도
Application은 유효한 최종 결과가 나오면 종료하고 모델 호출 8회·Tool 호출 시도 16회 제한 및 AGENT_TIMEOUT_SECONDS=60의 Agent 실행 Budget을 기본값으로 제공하며 설정 가능해야 한다(MUST). Budget 소진을 확인하면 추가 Agent 실행을 중단하고, 진행 중인 LLM HTTP I/O에 남은 Budget에 따른 timeout 및 취소를 적용해야 한다(MUST). 동기 DB 연결·SQL 실행에는 별도의 유한 timeout을 적용해야 한다(MUST). DB 대기는 Agent Budget을 초과할 수 있고 종료 저장을 포함한 전체 HTTP 응답의 엄격한 60초 상한은 보장하지 않는다. 거부·불완전 응답·한도 초과·LLM 통신 오류는 실패로 종료하고 SDK 재시도로 한도를 우회해서는 안 된다(MUST).

#### Scenario: 반복과 호출 한도
- **WHEN** 모델이 계속 Tool을 요청하여 다음 행동이 모델 호출 또는 Tool 호출 한도를 초과한다
- **THEN** 초과 행동을 실행하지 않고 limit_exceeded로 종료하며 그 전까지의 Trace를 보존한다

#### Scenario: 시간 한도와 LLM 실패
- **WHEN** Agent Budget 소진이 확인되거나 LLM timeout/통신 오류/거부/불완전 응답이 발생한다
- **THEN** 추가 호출을 중단하고 각각 execution_timeout, llm_error, model_refusal 또는 invalid_output으로 실패 기록을 남긴다

#### Scenario: 진행 중인 LLM HTTP I/O 취소
- **WHEN** LLM HTTP I/O 대기 중 남은 Agent Budget이 소진된다
- **THEN** 해당 I/O를 취소하고 execution_timeout으로 종료하며 추가 LLM/Tool 호출을 시작하지 않는다

#### Scenario: DB 대기의 별도 timeout
- **WHEN** 동기 DB 연결 또는 SQL 실행 대기가 남은 Agent Budget보다 길어진다
- **THEN** DB 대기는 별도의 유한 timeout을 따르며 DB 오류가 발생하면 support-requests의 DB 실패 계약을 적용하고, 정상 반환 후 Budget 소진을 확인하면 execution_timeout으로 추가 Agent 실행을 중단한다

### Requirement: 실행 Trace 및 비밀 정보 보호
시스템은 시작, 각 LLM 호출의 메타데이터·사용량·소요시간, 각 Tool의 검증된 입력/관찰 또는 오류, 최종 검증·종료 이벤트를 순서대로 저장해야 한다(MUST). 제공되지 않은 usage는 null로 유지해야 한다(MUST). 인증 토큰·API Key·DB 접속 정보·SQL·스택·모델 내부 추론 원문을 Trace와 오류 응답에 저장하거나 노출해서는 안 된다(MUST).

#### Scenario: 성공 및 실패 재구성
- **WHEN** 성공 또는 처리된 실패의 Trace를 조회한다
- **THEN** Tool 선택·관찰·종료 사유가 확인되고 LLM 메타데이터는 공급자 응답에서 확인된 값만 기록되며 비밀 설정과 내부 추론은 포함되지 않는다

### Requirement: 재현 가능한 자동 검증
시스템은 실제 OpenAI 통신 없이 Responses 응답을 대체하여 Loop와 API/저장을 자동 검증할 수 있어야 한다(MUST). 고정 고객 데이터·정책·응답 순서·기준 시각을 사용하여 주요 성공·실패·조직 경계 및 기존 Backend 회귀를 검증해야 한다(MUST). 실제 모델의 진단 품질과 성능 개선을 이 테스트만으로 주장해서는 안 된다(MUST).

#### Scenario: 자동 E2E 검증
- **WHEN** 고정된 복합 문의와 여러 차례의 Tool 선택·최종 응답으로 자동 테스트를 실행한다
- **THEN** HTTP 문의 생성부터 진단 및 별도 결과/Trace 조회까지 검증되고 기존 고객 데이터는 동일하며 실제 API Key가 필요하지 않다
