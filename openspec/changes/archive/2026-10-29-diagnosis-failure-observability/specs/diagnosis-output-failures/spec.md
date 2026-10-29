# Spec Delta

## Purpose

Agent와 Workflow의 유효하지 않은 출력이 거부된 단계와 사유를 안전한 구조화 정보로 관측한다. 기존 실행·오류 계약을 유지하면서 Evaluation에서 실패율과 분류별 내역을 구분하고, 기록되지 않은 과거 실패는 미분류로 보존한다.

## ADDED Requirements

### Requirement: 출력 실패 분류의 공통 계약
시스템은 새 실행의 기존 `invalid_output` 실패 분기마다 고정된 `failure_stage`, `failure_reason`과 nullable `field_path`를 기록해야 한다(MUST). 단계와 사유는 이 명세의 허용된 쌍이어야 하며 임의 문자열·추정 원인을 분류로 사용해서는 안 된다(MUST). 문의 응답의 오류 코드는 `invalid_output`으로 유지해야 한다(MUST).

#### Scenario: 두 Mode의 동일한 오류 계약
- **WHEN** Agent와 Workflow가 각각 서로 다른 출력 오류로 실패하고 종료 저장이 가능하다
- **THEN** 두 문의 모두 failed·diagnosis=null·error_code=invalid_output으로 저장되며 Trace의 구조화된 단계·사유로 발생 분기를 구분한다

#### Scenario: 분류 없는 legacy 실패
- **WHEN** 분류 정보가 없는 이전 기록이나 기존 오류 호출 경로를 처리한다
- **THEN** 오류 코드와 기존 실패 처리를 유지하고 metadata를 추정해서 생성하지 않는다

### Requirement: 응답 상태와 Mode별 출력 실패의 구분
응답 상태 거부는 `response_status/not_completed`, Agent call ID 오류는 `agent_tool_call/invalid_call_id` 또는 `agent_tool_call/duplicate_call_id`, Workflow의 Tool 요청은 `workflow_output/unexpected_function_call`, 빈 최종 문자열은 `text_extraction/empty_output`으로 기록해야 한다(MUST). 이 경우 field_path는 null이어야 한다(MUST).

#### Scenario: 완료되지 않은 응답
- **WHEN** Budget 소진이나 refusal이 우선 적용되지 않았고 응답 status가 completed가 아니거나 누락됐다
- **THEN** response_status/not_completed로 기록하고 후속 Tool·LLM 호출을 하지 않는다

#### Scenario: Agent call ID 형식과 중복
- **WHEN** Agent의 call ID가 누락/null/문자열 아닌 값/공백이거나, 유효 문자열이 같은 응답 또는 이전 턴의 ID와 중복된다
- **THEN** 형식 오류는 invalid_call_id, 중복은 duplicate_call_id로 구분하며 해당 응답의 Tool을 실행하지 않는다
- **THEN** 형식 오류와 중복이 함께 있으면 invalid_call_id가 우선한다

#### Scenario: Workflow의 예상하지 않은 Tool 호출
- **WHEN** Workflow 응답에 function_call이 포함된다
- **THEN** workflow_output/unexpected_function_call로 기록하며 응답에 텍스트가 없더라도 empty_output보다 우선한다

#### Scenario: 빈 최종 텍스트
- **WHEN** 어느 Mode든 Tool 처리 분기를 거친 뒤 최종 추출 문자열이 비어 있거나 공백뿐이다
- **THEN** text_extraction/empty_output으로 기록한다

### Requirement: JSON과 Diagnosis Schema 실패의 구분
비어 있지 않은 최종 문자열의 JSON 구문 오류는 `diagnosis_json/invalid_json`, 그 밖의 Diagnosis 필드·타입·필수 내용 검증 오류는 `diagnosis_schema/validation_error`로 구분해야 한다(MUST). 기존 Diagnosis의 수락·거부 조건을 변경해서는 안 된다(MUST).

#### Scenario: JSON 구문 오류
- **WHEN** 비어 있지 않은 최종 문자열이 잘린 JSON, JSON 밖의 설명 또는 잘못된 구문을 포함해 JSON 검증에 실패한다
- **THEN** diagnosis_json/invalid_json과 field_path=null을 기록하고 원문은 기록하지 않는다

#### Scenario: 필드와 필수 내용 오류
- **WHEN** JSON은 유효하지만 필수 필드 누락·추가 필드·잘못된 root/필드 타입·빈 문장·outcome별 필수 내용 위반이 있다
- **THEN** diagnosis_schema/validation_error와 안전하게 정규화한 첫 오류 위치 또는 null을 기록한다

### Requirement: 관찰 및 정책 참조 실패의 구분
관찰 참조 부재는 `evidence_reference/invalid_trace_reference`, diagnosed의 성공 관찰 부재는 `evidence_reference/no_successful_observation`으로 기록해야 한다(MUST). 잘못된 정책 출처는 `policy_reference/invalid_policy_reference`, 해당 검색에 없는 ID는 `policy_reference/unobserved_policy_document`여야 한다(MUST). 기존 관찰 구조 접근 오류는 `observation_validation/invalid_observation`으로 기록해야 한다(MUST).

#### Scenario: 실제 관찰이 아닌 Trace 참조
- **WHEN** Evidence가 없는 sequence, LLM 등 다른 이벤트 또는 observation이 없는 Tool Trace를 참조한다
- **THEN** invalid_trace_reference와 evidence[i].trace_sequence를 기록한다

#### Scenario: 성공 관찰 부재
- **WHEN** diagnosed의 Evidence가 유효한 관찰을 참조하지만 성공 관찰이 하나도 없다
- **THEN** no_successful_observation과 field_path=evidence를 기록한다

#### Scenario: 정책 출처와 ID 불일치
- **WHEN** 정책 ID가 있는 Evidence의 대상이 성공한 search_policy가 아니거나 해당 search_policy 결과에 지정 ID가 없다
- **THEN** 각각 invalid_policy_reference 또는 unobserved_policy_document와 evidence[i].policy_document_id를 기록한다
- **THEN** 다른 검색에서 그 ID를 관찰했더라도 현재 참조한 검색 결과의 검증을 생략하지 않는다

#### Scenario: 잘못된 관찰 구조
- **WHEN** 참조 검증 중 관찰의 필수 키·구조 접근에서 기존 invalid_output 대상 오류가 발생한다
- **THEN** invalid_observation과 안전한 Evidence 경로 또는 null을 기록하며 모델의 JSON 오류로 단정하지 않는다

### Requirement: 기존 종료 이벤트와 실행 경계 유지
출력 실패 metadata는 기존 `final_result(error_code=invalid_output)` 한 건에만 기록해야 한다(MUST). 새 이벤트를 도입하거나 `execution_failed`에 같은 metadata를 복제해서는 안 된다(MUST). Fail-fast·검증 우선순위·정상 요청 입력·호출 수·이벤트 순번·Diagnosis 결과와 비대상 오류 매핑을 유지해야 한다(MUST).

#### Scenario: 최종 검증 이전의 실패
- **WHEN** 응답 상태나 Mode별 출력 처리에서 invalid_output이 발생한다
- **THEN** final_result에 실제 failure_stage를 기록한 뒤 execution_failed로 끝나며 JSON 검증에 도달한 것으로 표시하지 않는다

#### Scenario: 성공과 비대상 오류 회귀
- **WHEN** 동일한 정상 Fake 응답을 실행하거나 Budget/refusal/LLM/Tool/DB 오류를 발생시킨다
- **THEN** 정상 결과·호출·sequence는 유지되고 새 필드는 null이며 비대상 실패의 기존 코드·우선순위가 유지된다
- **THEN** 분류를 위해 Retry·Recovery·추가 LLM 호출이나 새로운 응답 타입 거부를 도입하지 않는다

### Requirement: 실패 정보의 수집 제한
새 metadata는 고정 stage/reason과 허용된 Schema 필드·음이 아닌 배열 인덱스로 구성한 field_path 또는 null만 포함해야 한다(MUST). 응답/예외 원문·검증 input/ctx/msg·임의 추가 key·Secret·스택·모델 추론을 새 metadata·오류 로그·오류 응답에 기록해서는 안 된다(MUST). 허용되지 않는 오류 위치는 null로 표현해야 한다(MUST).

#### Scenario: 안전한 필드 위치
- **WHEN** 첫 Schema 오류가 evidence 배열 항목의 trace_sequence 또는 summary에 있다
- **THEN** 각각 evidence[i].trace_sequence 또는 summary로 정규화하고 실제 잘못된 값은 기록하지 않는다

#### Scenario: 추가 필드와 예외의 민감정보
- **WHEN** 합성 민감정보 표식이 추가 JSON key, 검증 값, 예외 메시지/ctx, call ID 또는 정책 ID에 들어 있다
- **THEN** 새 metadata·오류 응답·캡처한 로그·실패 분류 산출물에는 표식이 없고 임의 key의 field_path는 null이다

### Requirement: Evaluation 원본 보존과 이전 결과 호환
Evaluation은 새 Trace metadata를 executions.json에 보존하고 새 Reader는 필드가 없는 이전 결과를 null로 읽어야 한다(MUST). 기존 원본·hash·Diagnosis·오류 코드·측정값은 유지해야 한다(MUST). 과거 실패 사유를 Token·Case·Mode·시간으로 추정하거나 실제 진단·Judge를 재호출해서 보충해서는 안 된다(MUST).

#### Scenario: 새 Trace의 round-trip
- **WHEN** 두 Mode의 출력 실패 Trace를 평가 결과로 직렬화하고 다시 읽는다
- **THEN** failure_stage/reason/path가 같고 기존 Trace·문의·측정 필드는 보존된다

#### Scenario: 이전 결과 읽기와 재채점 준비
- **WHEN** 분류 필드 없는 합성 이전 결과를 읽거나 별도 재채점 디렉터리를 준비한다
- **THEN** 원본 byte/hash는 유지되고 누락 metadata는 null이며 진단·Judge 호출 없이 준비할 수 있다

### Requirement: 실행 단위의 출력 실패 집계
Evaluation의 Mode 및 Case×Mode 요약은 invalid_output의 count·rate·classified_count·unclassified_count·by_reason을 제공해야 한다(MUST). 분자는 started인 failed 실행 중 저장된 문의가 failed/invalid_output인 수, 분모는 해당 그룹의 모든 started 실행 수여야 한다(MUST). 기존 실행·품질·효율 지표 정의와 Judge 입력을 변경해서는 안 된다(MUST).

#### Scenario: 분류와 중복 방지
- **WHEN** 대상 실패에 유일한 invalid_output final_result와 유효한 metadata가 있다
- **THEN** 해당 stage/reason을 실행당 한 번만 세고 execution_failed는 중복 집계하지 않는다
- **THEN** by_reason은 stage/reason으로 정렬된 count 항목 배열이며 0건 항목은 생략한다

#### Scenario: 미기록 및 모호한 Trace
- **WHEN** 대상 실패의 final_result가 없거나 여러 건이거나 metadata가 없다
- **THEN** unclassified_count로 세고 classified_count와의 합은 count이며 by_reason 건수 합은 classified_count다

#### Scenario: 불완전 Trace와 최종 오류
- **WHEN** Trace 일부가 없어도 유일한 canonical final_result의 metadata가 있거나, 최종 문의 오류가 database_error이거나 실행이 incomplete다
- **THEN** 첫 경우는 확인된 분류를 사용하고 나머지는 Trace의 invalid_output만으로 출력 실패 분자에 포함하지 않는다

#### Scenario: 분모와 기존 지표 보존
- **WHEN** started 5건 중 출력 실패 2건이 있고 그중 한 건만 분류되며 나머지는 성공·incomplete·다른 실패다
- **THEN** count=2, rate=2/5, classified_count=1, unclassified_count=1이고 기존 실행·품질·효율 값은 동일하다
- **THEN** started가 0이면 rate.value=null이며 출력 실패 0건이면 count와 두 분류 수는 0, by_reason은 빈 배열이다

### Requirement: 실제 API 없는 실패 관측성 검증
분류·실패 전달·저장/조회·정보 보호·집계·호환과 성공 회귀를 Fake Client·합성 오류·고정 시계·격리 테스트 데이터로 검증할 수 있어야 한다(MUST). 실제 API Key·진단/Judge 호출·50회 Evaluation 재실행을 자동 검증에 요구해서는 안 된다(MUST).

#### Scenario: 재현 가능한 검증
- **WHEN** 주요 출력 실패와 정상 script를 자동 테스트한다
- **THEN** 외부 LLM 호출 없이 분류와 기존 실행 계약이 검증되며 Fake 통과를 실제 실패율 감소나 C05 원인 누락 개선으로 보고하지 않는다
