# workflow-diagnosis Specification

## Purpose

고객 지원 문의를 Application이 정한 조회 순서와 분기로 처리하고, 수집한 사실과 정책을 단일 LLM 호출로 진단한다. Agent와 공통 권한·데이터·결과 계약을 사용하면서 시스템 수준 Trade-off를 평가할 첫 Fixed-step Workflow Baseline을 제공한다. 모델 호출 수·Context 구성·조회 범위의 차이를 포함하므로 동적 Tool 선택만의 인과적 효과나 Workflow/Agent 전체의 일반적 우열을 검증하는 명세가 아니다.

## Requirements

### Requirement: 고정 조회와 단일 LLM 진단
Workflow는 문의 생성자, 인증된 조직, 구독, 결제 이력을 순서대로 조회한 뒤 `플랜`, `권한`, `구독 결제` 검색어로 정책을 순서대로 검색해야 한다(MUST). 정상 또는 부재 관찰을 모두 수집한 뒤 LLM을 최대 한 번 호출해야 한다(MUST). LLM에 Tool 선택을 허용하거나 출력에 따라 재조회·재시도·수정 호출을 해서는 안 된다(MUST).

#### Scenario: 복합 문의의 고정 실행
- **WHEN** 팀원 초대 문제를 workflow로 진단한다
- **THEN** 사용자·조직·구독·결제 및 세 정책 검색의 일곱 관찰이 순서대로 생성되고 단일 LLM 입력에 문의 내용·대상 식별자·기준 UTC와 함께 전달된다

#### Scenario: 문의 내용에 무관한 조회 순서
- **WHEN** 단순 정책 문의 또는 다른 Tool을 호출하라는 지시를 포함한 문의를 workflow로 진단한다
- **THEN** 동일한 일곱 조회를 수행하고 문의 본문이나 LLM이 조회 종류·순서·대상·검색어를 변경하지 않는다

#### Scenario: 예상하지 않은 Tool 요청
- **WHEN** Workflow의 LLM 응답에 Tool 호출이 포함된다
- **THEN** Tool을 실행하지 않고 invalid_output으로 실패하며 추가 LLM 호출을 하지 않는다

### Requirement: 공통 데이터와 권한 경계
Workflow는 기존 고객 조회·정책 검색 Service의 필드·정렬·검색 결과와 읽기 전용 계약을 그대로 사용해야 한다(MUST). 인증된 CallerContext로 조직 범위를 강제하고 기본 대상은 실행자가 아닌 문의 생성자여야 한다(MUST). 본문과 LLM 출력의 식별자·지시로 접근 범위를 변경해서는 안 된다(MUST).

#### Scenario: 같은 조직의 다른 실행자
- **WHEN** admin이 같은 조직 member의 초대 문의를 workflow로 실행한다
- **THEN** member의 역할과 인증된 조직 데이터가 조회되고 admin의 역할로 진단 대상을 대체하지 않는다

#### Scenario: 교차 조직 지시와 읽기 전용성
- **WHEN** 다른 조직의 ID 조회나 실제 환불·초대를 요구하는 본문으로 Workflow를 실행한다
- **THEN** 해당 조직 데이터는 모델 입력·Trace·근거에 포함되지 않으며 고객·구독·결제·정책 레코드는 변경되지 않는다

### Requirement: 부재 관찰과 기술 오류 분기
조회 not_found는 안전한 부재 관찰로, 빈 결제·정책 결과는 정상 빈 관찰로 전달하고 고정 후속 단계를 계속해야 한다(MUST). 만료·past_due·실패/pending 결제는 업무 데이터로 전달해야 한다(MUST). 인증 컨텍스트 오류·정책 로딩 실패·DB 실패·예상하지 못한 조회 오류는 후속 조회와 LLM 호출을 중단해야 한다(MUST).

#### Scenario: 구독 부재와 빈 결과
- **WHEN** 구독이 없고 결제 이력 또는 검색 결과가 비어 있다
- **THEN** 부재/빈 관찰을 구분해 남기고 남은 조회와 단일 진단을 수행하며 정보 부족만으로 실행을 failed 처리하지 않는다

#### Scenario: 업무 상태의 보존
- **WHEN** past_due 구독과 failed 결제 또는 상태와 만료 시각이 불일치하는 구독을 조회한다
- **THEN** 저장된 상태와 시각을 그대로 모델에 전달하고 결제 상태만으로 구독 상태를 덮어쓰지 않는다

#### Scenario: 치명적 조회 오류
- **WHEN** 정책 로딩이나 고객 조회 중 내부 오류 또는 DB 오류가 발생한다
- **THEN** 기술 오류를 빈 결과로 바꾸지 않고 수집 기록을 보존하여 중단하며 각각 tool_error 또는 support-requests의 DB 실패 계약을 적용한다

### Requirement: 구조화된 결과와 근거 검증
Workflow 결과는 기존 Agent와 동일한 outcome·summary·causes·next_actions·evidence 계약과 Application의 관찰/정책 참조 검증을 통과해야 한다(MUST). diagnosed는 원인과 성공 관찰 근거를, needs_information/escalate는 질문/이관 안내를 포함해야 한다(MUST). 잘못된 출력은 invalid_output으로 실패해야 한다(MUST).

#### Scenario: 실제 관찰을 참조한 진단
- **WHEN** 모델이 고정 조회에서 반환된 사용자·구독·정책 관찰을 참조하는 유효한 진단을 반환한다
- **THEN** 같은 실행의 trace_sequence와 실제 검색된 policy_document_id에 연결된 결과를 completed로 저장한다

#### Scenario: 정보 부족과 지원 범위 밖
- **WHEN** 모델이 유효한 needs_information 또는 escalate 결과를 반환한다
- **THEN** 질문 또는 이관 안내를 completed로 저장하며 실제 상태 변경은 수행하지 않는다

#### Scenario: 출력 형식과 근거 위조
- **WHEN** 출력이 비어 있거나 스키마를 위반하거나 없는 관찰/정책을 참조하거나 성공 관찰 없이 diagnosed를 반환한다
- **THEN** diagnosis를 저장하지 않고 invalid_output과 검증 실패 Trace를 저장한다

### Requirement: 유한 실행과 실패 종료
Workflow 실행 Budget은 기본 60초이며 양의 유한 값으로 설정 가능해야 한다(MUST). Budget 소진을 확인하면 후속 작업을 중단하고 진행 중인 LLM I/O에는 남은 Budget에 따른 timeout·취소를 적용해야 한다(MUST). DB에는 별도 유한 timeout을 적용해야 한다(MUST). SDK 재시도는 비활성화하고 거부·불완전 응답·통신 오류를 실패로 종료해야 한다(MUST).

#### Scenario: 시간 초과와 호출 중단
- **WHEN** 조회 전후 또는 LLM 대기 중 실행 Budget이 소진된다
- **THEN** execution_timeout으로 중단하고 진행 중인 LLM I/O를 취소하며 후속 작업과 재시도를 시작하지 않는다

#### Scenario: 모델 오류 분류
- **WHEN** 모델이 응답을 거부하거나 불완전 응답을 주거나 Budget 소진 이외의 timeout/통신 오류가 발생한다
- **THEN** 각각 model_refusal, invalid_output, llm_error로 실패하고 재호출하지 않는다

#### Scenario: DB 대기와 저장 시간의 한계
- **WHEN** 동기 DB 대기 또는 종료 저장이 실행 Budget보다 오래 걸린다
- **THEN** 별도 DB timeout과 support-requests의 저장 계약을 적용하며 전체 HTTP 응답의 엄격한 60초 상한을 보장하지 않는다

### Requirement: 비교에 필요한 실행 관찰
Workflow는 기존 Trace 형식으로 시작·조회 관찰/오류·LLM 메타데이터와 사용량/시간·최종 검증·종료를 순서대로 저장해야 한다(MUST). 실제 조회를 tool_result로 기록하고 같은 실행의 근거와 연결해야 한다(MUST). Token 사용량은 공급자가 제공한 usage를 사용하고 미제공 usage는 null이어야 한다(MUST). 비밀 설정·SQL·스택·모델 내부 추론은 저장하지 않아야 한다(MUST).

#### Scenario: Workflow 기록의 재구성
- **WHEN** 완료 또는 처리된 실패 실행의 결과와 Trace를 조회한다
- **THEN** execution_mode와 UTC 시작/종료 시각 및 순서 있는 Trace로 실행 방식·실제 조회 횟수·각 조회 결과·최대 한 번의 LLM 시도·종료 사유를 확인할 수 있다

#### Scenario: 사용량 부재와 오류 보호
- **WHEN** 공급자가 usage를 주지 않거나 LLM/조회 오류에 내부 정보가 포함된다
- **THEN** 사용량을 0으로 추정하지 않고 null로 남기며 안전한 오류 코드만 노출한다

#### Scenario: 관찰값과 시간 범위의 구분
- **WHEN** 고정 시계와 Fake 공급자 usage를 사용한 실행의 Request와 Trace를 조회한다
- **THEN** 실제 조회/LLM 시도 수와 공급자 usage, Request 시작/종료 차이의 실행 경과시간 및 Trace의 단계별 duration_ms를 각각 확인할 수 있고 단계 합계를 전체 실행 또는 HTTP 지연으로 대체하지 않는다

이 관찰값만으로 진단의 의미적 정확성·정책 준수·안전성·근거의 타당성이나 조회의 필요성을 보장하지 않는다. 비용 환산·실패율 집계·채점과 비교 결과 해석은 별도 Evaluation 작업이며 이번에는 Metrics DB·집계 테이블·평가 실행기를 추가하지 않는다.

### Requirement: 실제 API 없는 재현 가능한 검증
시스템은 실제 OpenAI API Key 없이 Fake Responses, 고정 고객/정책 데이터·기준 UTC·시계를 사용해 Workflow의 HTTP 접수부터 결과/Trace 조회와 주요 실패·권한 경계를 검증할 수 있어야 한다(MUST). 같은 문의 내용·생성자·조직·실행자 권한·고객 데이터·정책 버전·모델과 가능한 범위의 공통 생성 설정, 비교 가능한 기준 UTC를 사용한 Agent 회귀도 검증해야 한다(MUST). 방식별 실제 조회 범위·Context·호출 횟수는 보존하고 이 테스트로 진단 품질이나 성능 우위를 주장해서는 안 된다(MUST).

#### Scenario: 자동 E2E와 공통 조건
- **WHEN** 같은 생성자·내용의 별도 문의를 고정 데이터·정책·시각에서 Agent와 Workflow로 각각 실행하는 테스트를 수행한다
- **THEN** 같은 조회 Service·출력 검증·저장/권한 계약과 모델·공통 생성 설정이 적용되고 입력의 기준 UTC가 일치하며 각 방식의 실제 조회/LLM 호출·Context와 실행 기록은 분리되어 실제 네트워크 호출 없이 검증된다
