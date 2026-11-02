# Spec Delta

## ADDED Requirements

### Requirement: 문의 시점 범위가 고정된 지식 베이스 Tool
시스템은 `search_documents`, `get_document`, `search_threads`, `get_thread` 네 Tool을 제공해야 하며, 각 Tool은 Application이 넘긴 `KnowledgeScope`로 지식 베이스 Service를 호출해야 한다(MUST). Tool 인자는 strict schema로 검증하고 source, snapshot, as_of, 제외 스레드를 받아서는 안 된다(MUST). 관찰은 `{ok, data, error_code, trace_sequence}` 형식이어야 하며, 스레드는 `<kind>-<number>` 형식의 ref로 식별해야 한다(MUST). Service의 not_found와 인자 오류는 실행을 중단하지 않는 실패 관찰로 반환하고, DB 오류는 실행 실패로 처리해야 한다(MUST). 모든 호출은 인자와 관찰을 `tool_result` Trace로 남겨야 한다(MUST).

#### Scenario: 범위 인자 거부
- **WHEN** Tool 인자에 as_of, snapshot_id 또는 알 수 없는 필드가 포함된다
- **THEN** `invalid_arguments` 실패 관찰을 반환하고 Service를 호출하지 않는다

#### Scenario: 미래·제외 스레드
- **WHEN** as_of 이후 스레드나 제외 스레드의 번호로 `get_thread`를 호출한다
- **THEN** 존재하지 않는 번호와 같은 `not_found` 실패 관찰을 반환하고 실행은 계속된다

#### Scenario: 관찰과 Trace
- **WHEN** 문서와 스레드를 검색하고 조회한다
- **THEN** 관찰에는 문서 path와 스레드 ref가 들어 있고 순위 점수는 없으며, 각 호출의 인자와 관찰이 순서대로 `tool_result` Trace에 기록된다

### Requirement: 관찰에 연결된 인용을 가진 지식 답변 계약
두 실행 경로는 outcome(answered, insufficient_knowledge), answer, citations로 구성된 `KnowledgeAnswerDTO`를 반환해야 한다(MUST). 각 인용은 이번 실행의 성공한 지식 베이스 `tool_result`를 trace_sequence로 가리켜야 하고, ref는 그 관찰에 실제로 나온 문서 path 또는 스레드 ref여야 하며, source는 관찰한 Tool 종류와 일치해야 한다(MUST). answered는 인용이 1개 이상이어야 한다(MUST). 이를 어기면 실패 단계와 사유를 기록한 `invalid_output`으로 실패해야 한다(MUST).

#### Scenario: 관찰되지 않은 인용
- **WHEN** 답변이 실제 관찰 trace_sequence를 가리키지만 그 관찰에 없는 문서 path를 인용한다
- **THEN** `invalid_output`, 실패 단계 `citation_reference`, 사유 `unobserved_citation`으로 실패한다

#### Scenario: 존재하지 않거나 실패한 관찰 참조
- **WHEN** 인용의 trace_sequence가 Tool 관찰이 아니거나, `ok=false`인 관찰이거나, source가 관찰한 Tool 종류와 다르다
- **THEN** `invalid_output`, 실패 단계 `citation_reference`, 사유 `invalid_citation_reference`로 실패한다

#### Scenario: 근거 없는 answered
- **WHEN** outcome이 answered이고 citations가 비어 있다
- **THEN** `invalid_output`으로 실패한다

#### Scenario: 근거 부족
- **WHEN** outcome이 insufficient_knowledge이고 citations가 비어 있다
- **THEN** 유효한 답변으로 반환된다

### Requirement: Agent 지식 답변 실행
Agent 경로는 네 지식 베이스 Tool을 사용해 관찰에 따라 추가 검색·조회 또는 최종 답변을 판단해야 한다(MUST). 기존 Agent Step 수, Tool 호출 수, 실행 Timeout 설정을 적용하고, 한도를 넘으면 `limit_exceeded`로 실패해야 한다(MUST). 입력에는 as_of와 문의 원문을 포함하고, 지시에는 관찰을 지시가 아닌 데이터로 취급하라는 규칙을 포함해야 한다(MUST).

#### Scenario: 검색어 수정 후 답변
- **WHEN** Fake 모델이 결과 없는 검색 → 다른 검색어로 검색 → 스레드 조회 → 그 스레드를 인용한 답변을 순서대로 반환한다
- **THEN** 세 Tool 관찰과 최종 답변이 Trace에 기록되고 검증된 `KnowledgeAnswerDTO`가 반환된다

#### Scenario: Tool 호출 한도
- **WHEN** Fake 모델이 최종 답변 없이 Tool 호출을 계속 요청한다
- **THEN** 설정한 Step 또는 Tool 호출 한도에서 `limit_exceeded`로 실패한다

### Requirement: 고정 RAG Workflow 지식 답변 실행
Workflow 경로는 LLM을 정확히 두 번 호출해야 한다(MUST). 첫 호출은 검색어 1~3개를 생성하고, Application은 검색어마다 문서·스레드를 limit 5로 검색한 뒤, 검색어 순서와 순위 순서로 번갈아 모은 중복 없는 문서 최대 3개와 스레드 최대 3개를 조회해야 한다(MUST). 두 번째 호출은 Tool 없이 모든 관찰로 답변해야 한다(MUST). 검색어 수나 형식이 유효하지 않으면 실패 단계 `workflow_queries`, 사유 `invalid_queries`의 `invalid_output`으로 실패해야 한다(MUST).

#### Scenario: 결정적 검색·조회
- **WHEN** 같은 지식 베이스에서 Fake 모델이 같은 검색어 2개를 반환하는 Workflow를 두 번 실행한다
- **THEN** 두 실행의 Tool 호출 순서와 인자가 같고, 조회한 문서·스레드는 각각 3개 이하이며 중복이 없고, LLM 호출은 각각 2회다

#### Scenario: 잘못된 검색어
- **WHEN** 첫 호출이 검색어 0개, 4개, 또는 빈 문자열을 반환한다
- **THEN** Tool을 호출하지 않고 `workflow_queries`·`invalid_queries`로 실패한다

#### Scenario: 답변 단계의 Tool 호출
- **WHEN** 두 번째 호출이 function call을 반환한다
- **THEN** `invalid_output`, 사유 `unexpected_function_call`로 실패한다

### Requirement: 공통 지식 답변 진입점
`answer_question`은 호출자가 만든 `KnowledgeScope`, 문의 원문, 실행 모드를 받아 해당 경로를 실행하고, 두 모드에 같은 출력 검증, Trace 이벤트 형식, 실패 코드를 적용해야 한다(MUST). 결과는 상태, 검증된 답변 또는 오류 코드·실패 메타데이터, 전체 Trace를 반환하며 DB에 저장해서는 안 된다(MUST). 기존 계정 진단의 LLM 요청 내용과 결과는 바뀌어서는 안 된다(MUST).

#### Scenario: 모드별 동일 계약
- **WHEN** 같은 Fake 답변으로 agent와 workflow 모드를 각각 실행한다
- **THEN** 두 결과 모두 같은 `KnowledgeAnswerDTO` 검증을 거치고 `execution_started`로 시작해 `execution_completed`로 끝나는 Trace를 반환한다

#### Scenario: 기존 진단 회귀
- **WHEN** 기존 계정 진단 Agent와 Workflow를 실행한다
- **THEN** LLM 요청의 출력 스키마는 `DiagnosisDTO`이고 기존 테스트가 그대로 통과한다
