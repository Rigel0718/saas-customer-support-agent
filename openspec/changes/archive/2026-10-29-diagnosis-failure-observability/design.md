# Design

## Context

동기는 [Proposal](proposal.md)의 Why를 따른다. 이번 문서는 제안 설계이며 아래의 현재 동작과 구분한다.

| 현재 구현 근거 | 확인한 사실 |
|---|---|
| `app/diagnosis/llm.py:94–193`, `call_model()` / `extract_output_text()` | 응답 메타데이터 Trace를 기록한 뒤 Budget·refusal·status를 검사한다. `status != completed`는 `invalid_output`이다. 최종 텍스트는 message/output_text를 연결한 문자열이다. |
| `app/agent/loop.py:76–85`, `run_agent()` | Tool call이 없으면 문자열을 반환하고, 있으면 call ID 누락·타입·공백·중복을 검사한다. |
| `app/workflow/runner.py:97–102`, `run_workflow()` | function_call과 빈 최종 문자열을 거부한다. |
| `app/services/support.py:96–123`, `validate_diagnosis()` | Pydantic JSON/Schema 및 실제 관찰·정책 참조를 검증한다. ValidationError/ValueError/TypeError/KeyError를 하나의 `ExecutionFailure`로 축약한다. |
| `app/services/support.py:223–259`, `diagnose_request()` | 검증 실패와 Runner의 조기 실패 모두 `final_result → execution_failed`를 만들고 함께 종료 저장한다. |
| `app/schemas/support.py:111–130`, `app/models/support.py:64–93` | nullable `llm_metadata`는 요청 Timeout과 `llm_error` 예외 분류를 담는다. 출력 실패 정보를 담는 필드는 없다. |
| `evaluation/results.py`, `Execution` / `ResultStore` | TraceDTO를 중첩 직렬화하며 `evaluation-v1` Reader는 미지 필드를 거부한다. 재채점은 원본 hash를 연결해 별도 디렉터리에 저장한다. |
| `evaluation/metrics.py`, `summarize()` | Mode 및 Case×Mode 그룹에 실행 성공·실패와 기존 지표를 집계한다. 출력 실패 사유별 집계는 없다. |

과거 세 실행은 응답 원문과 구체적인 검증 오류가 없어 재분류할 수 없다. Token 수나 `llm_response.error_code=null`은 `completed`·유효 JSON의 증거가 아니다. 이번 변경은 앞으로 발생하는 Application의 거부 분기를 식별하며, 공급자 내부 원인이나 출력 잘림 원인까지 증명하지 않는다.

`agent-workflow-evaluation`과 `improve-llm-execution-reliability`는 구현 완료·미아카이브 상태다. 코드에 존재하는 두 계약을 전제로 하되 해당 delta를 수정하지 않는다. 이전 Evaluation 설계의 Runtime 불변 원칙은 그 Change의 범위였으며, 이번 Change는 출력 검증의 허용/거부 조건을 유지한 채 분류 전달만 명시적으로 추가한다.

## Goals / Non-Goals

**Goals:** 실패를 처음 확인하는 계층에서 고정된 분류를 만들고, 기존 종료 저장과 Trace 조회 및 Evaluation까지 손실 없이 전달한다. 정상 경로의 입력·호출·Diagnosis·이벤트 순번, 실패의 우선순위 및 기존 오류 코드를 유지한다.

**Non-Goals:** 응답/예외 원문 저장, 공급자 응답 구조의 새로운 엄격 검증, 기존 `tool_error`의 재분류, Pydantic validator·Diagnosis JSON Schema 변경은 하지 않는다. Proposal의 Retry·실행 전략·품질 개선 제외 범위도 유지한다.

## Decisions

### D1. 기존 final_result에 nullable failure_metadata 하나를 추가한다

`TraceDTO.failure_metadata`와 `AgentTrace.failure_metadata`에 각각 작은 Pydantic DTO와 nullable JSONB 열을 추가한다. 모델은 `extra="forbid"`를 사용하고 아래 필드만 허용한다.

```json
{
  "failure_stage": "evidence_reference",
  "failure_reason": "invalid_trace_reference",
  "field_path": "evidence[0].trace_sequence"
}
```

stage/reason은 D3의 허용된 **쌍**으로 제한한다. `field_path`는 null 또는 D4의 정규화된 경로다. 임의 details dict, 메시지, 원본 값, response status 원문, call ID 원문, 정책 ID 원문은 추가하지 않는다.

새 실행의 `invalid_output`은 **기존 `final_result` 한 건에만** 메타데이터를 기록한다. `execution_failed`는 기존 오류 코드만 유지하고, `llm_response`·Tool·정상 final_result는 새 필드가 null이다. 결과적으로 이벤트 종류·개수·순서·관찰 sequence가 바뀌지 않는다. `final_result`는 실행 출력 실패의 관찰 지점이며 최종 JSON 검증 도달 여부는 `failure_stage`로 판별한다.

대안인 별도 failure 이벤트는 이벤트 수·sequence·Consumer 처리 범위를 늘린다. `llm_metadata.error` 재사용은 LLM 예외·최종 검증 책임을 섞고, 종료 이벤트 두 곳에 복제하면 집계 중복과 불일치 위험이 있다. 기존 final_result와 한 nullable 열이 가장 작은 공통 경계다.

### D2. 발생 계층은 분류하고 Service는 기존 종료 기록을 소유한다

변경 전:

```text
공통 LLM / Mode Runner / 최종 검증
  → ExecutionFailure("invalid_output")  # 세부 정보 소실
  → Service: final_result(invalid_output), execution_failed(invalid_output)
  → 기존 save_terminal → DB → Trace API → Evaluation
```

변경 후:

```text
공통 LLM / Mode Runner / 최종 검증
  → ExecutionFailure("invalid_output", failure_metadata=<고정 분류>)
  → Service: final_result(invalid_output, failure_metadata) 한 건
             execution_failed(invalid_output) 한 건
  → 기존 save_terminal → DB → Trace API → executions.json
  → summarize: 실행당 하나의 canonical final_result만 분류 집계
```

`ExecutionFailure`에 keyword-only optional `failure_metadata=None`을 추가한다. 기존 `ExecutionFailure(code)`와 `str(exc)==code`를 보존하고 metadata를 예외 메시지에 포함하지 않는다. 별도 exception hierarchy·registry·callback은 만들지 않는다. `invalid_output` 이외 오류에는 새 metadata를 붙이지 않는다. legacy 호출에서 metadata가 없더라도 기존 실패 처리를 유지하고 추정 분류를 만들지 않는다.

| 책임 | 담당 |
|---|---|
| DTO의 허용 stage/reason/path 검증 | `app/schemas/support.py` |
| 기존 오류 코드와 선택적 metadata 전달 | `app/diagnosis/runtime.py`, `ExecutionFailure` |
| Budget/refusal 우선순위 이후 non-completed 분류 | 공통 `call_model()` |
| call ID 유효성·중복 분류 | Agent `run_agent()` |
| 예상하지 않은 function_call, 공백/빈 텍스트 분류 | Workflow `run_workflow()` |
| JSON/Schema·관찰·정책 참조 분류 | 공통 `validate_diagnosis()` |
| metadata가 있는 final_result의 중복 없는 기록, 기존 원자적 종료 저장 | `diagnose_request()` / `save_terminal()` |
| Trace 보존·실행별 중복 없는 사유 집계 | 기존 Evaluation 결과 계약 / `summarize()` |

Service의 검증 직후 예외 처리와 바깥 ExecutionFailure 처리 모두 metadata를 전달한다. 이미 final_result가 있으면 추가하지 않는 기존 동작을 유지한다. metadata로 정상 성공을 실패시키거나 실패를 복구하지 않는다. DB 저장 실패 시 기존 rollback·503·종료 복구 조회/저장 경로를 유지한다. 최종 문의 오류가 `database_error`이면 중간 final_result의 출력 실패 정보를 남길 수 있지만 Evaluation의 `invalid_output` 분자에 넣지 않는다.

### D3. 실제 분기에 근거한 작은 Taxonomy를 고정한다

| failure_stage | failure_reason | 기존 판정 조건 / field_path |
|---|---|---|
| `response_status` | `not_completed` | `call_model()`의 `status != completed`. path=null |
| `agent_tool_call` | `invalid_call_id` | call_id 누락/null/문자열 아님/공백. path=null |
| `agent_tool_call` | `duplicate_call_id` | 같은 응답 또는 이전 턴의 ID 재사용. path=null |
| `workflow_output` | `unexpected_function_call` | Workflow 응답에 function_call 존재. path=null |
| `text_extraction` | `empty_output` | 추출 문자열이 빈 값/공백. path=null |
| `diagnosis_json` | `invalid_json` | 비어 있지 않은 문자열에 대한 Pydantic JSON 구문 오류. path=null |
| `diagnosis_schema` | `validation_error` | 그 밖의 Pydantic 필드·타입·extra·outcome별 validator 실패. 안전한 loc 또는 null |
| `evidence_reference` | `invalid_trace_reference` | observation이 있는 tool_result가 아닌 sequence 참조. `evidence[i].trace_sequence` |
| `evidence_reference` | `no_successful_observation` | diagnosed의 Evidence 중 성공 관찰 없음. `evidence` |
| `policy_reference` | `invalid_policy_reference` | 정책 ID를 지정했으나 해당 Trace가 성공한 search_policy가 아님. `evidence[i].policy_document_id` |
| `policy_reference` | `unobserved_policy_document` | 해당 search_policy 결과에 지정 ID가 없음. `evidence[i].policy_document_id` |
| `observation_validation` | `invalid_observation` | 기존 TypeError/KeyError 등으로 처리되던 관찰 구조 접근 실패. 해당 Evidence 경로 또는 null |

JSON 파싱과 Schema를 구분해야 하므로 후보의 `diagnosis_schema`를 두 stage로 나눈다. 정책 검증의 두 명시 분기를 구분하고, 정상 관찰이 없는 진단 및 잘못된 관찰 구조도 기존 실패 경로이므로 포함한다. 이외 범용 unknown 분류를 새 Runtime 실패 분기로 만들지 않는다. metadata 없는 과거/legacy 실패는 집계에서만 `unclassified_count`로 남긴다.

분류 부착 시 지킬 세부 사항:

- `call_model()`의 llm_response 생성·Budget 확인·refusal·status 순서를 유지한다. refusal 또는 Budget 소진을 `invalid_output`으로 바꾸지 않는다. status 원문·공급자의 incomplete 사유를 수집하는 확장은 이번 최소 설계에서 제외한다.
- Agent는 우선 ID의 타입/빈 값 여부를 검사한 다음 중복을 확인한다. 둘 다 있으면 `invalid_call_id`가 우선한다. 모든 ID가 유효한 문자열일 때만 집합 연산을 수행하며, 수락되는 호출과 차단 시점은 동일하다.
- Workflow는 function_call 검사를 빈 텍스트 검사보다 먼저 유지한다. Agent는 기존처럼 텍스트를 Service에 반환하고, `validate_diagnosis()`에서 빈/공백 문자열을 먼저 `empty_output`으로 분류한다. 텍스트 연결 방식은 바꾸지 않는다.
- JSON 파싱을 별도로 반복하지 않는다. 기존 `model_validate_json()`의 ValidationError에서 안전한 type만 검사해 `json_invalid`를 JSON 분기로 구분하고 다른 오류는 Schema 분기로 보낸다. 기존 `required_content` validator와 생성 JSON Schema는 변경하지 않는다.
- 기존 참조 검사 순서를 유지하며 명시적인 ValueError 발생 지점에 해당 metadata를 부착한 ExecutionFailure를 사용한다. 관찰 접근의 기존 TypeError/KeyError/기타 ValueError 변환 경계는 작은 try/except로 `invalid_observation`을 전달한다. 오류 메시지 문자열을 매칭해 분류하지 않는다.
- 응답 item에 `.get()`이 없거나 output_text 값의 타입 때문에 join이 실패하는 등 기존 일반 예외의 `tool_error` 매핑은 유지한다. 새 taxonomy를 명목으로 공급자 응답의 허용/거부 범위를 확장하지 않는다.

### D4. field_path는 Schema 이름과 배열 인덱스만 보존한다

Pydantic 오류 전체나 `str(exc)`, `repr`, args, traceback, cause chain을 기록하지 않는다. 필요한 오류 목록은 `errors(include_input=False, include_context=False, include_url=False)`로 얻고 첫 번째 항목의 `type`·`loc`만 사용한다. `msg`는 저장·로그하지 않는다. JSON과 Schema를 구분한 뒤 Schema의 첫 오류 위치만 사용하며, 오류 전체 목록은 보존하지 않는다.

허용 경로는 `outcome`, `summary`, `causes`, `next_actions`, `evidence`, `causes[i]`, `next_actions[i]`, `evidence[i]`, `evidence[i].trace_sequence`, `evidence[i].policy_document_id`, `evidence[i].statement`뿐이다. i는 음이 아닌 정수이며 bool은 제외한다. DTO도 이 형식을 제한한다. extra 필드 이름처럼 loc에 임의 문자열이 들어 있거나 root model validator의 loc가 비어 있으면 `field_path=null`로 둔다. 알려진 경로만 재구성하며 임의 loc를 이어 붙이거나 잘라서 저장하지 않는다.

예외 입력·추가 JSON key·refusal 문구·call ID·정책 ID에 넣은 합성 민감정보 표식이 새 metadata·오류 응답·캡처한 로그에 남지 않음을 테스트한다. 기존 정상 Tool 관찰·문의·유효 Diagnosis 저장은 그대로이며 이 변경이 기존 데이터 전체를 익명화한다는 의미는 아니다.

Trade-off는 상세 validation 값과 전체 메시지 없이 거부 단계·사유·필드만 식별한다는 것이다. 원문 기반의 정확한 출력 재현은 제공하지 않으며, 이를 해결하려고 원문 보존 기능을 함께 도입하지 않는다.

### D5. Evaluation에는 기존 요약 그룹의 additive breakdown을 넣는다

`Execution.traces: list[TraceDTO]`와 기존 model_dump/Reader를 통해 `executions.json.items[].traces[].failure_metadata`를 보존한다. 별도 execution-level metadata 사본이나 새 산출물·CLI 명령은 만들지 않는다. `evaluation-v1`을 유지하고 새 Reader가 과거 누락 필드를 null로 읽는다.

`summarize()`의 `by_mode[mode]` 및 `by_case_mode[case:mode]` 각각에 다음 블록을 추가한다. 아래는 **합성 예시**다.

```json
{
  "invalid_output": {
    "count": 3,
    "rate": {"numerator": 3, "denominator": 25, "value": 0.12},
    "classified_count": 2,
    "unclassified_count": 1,
    "by_reason": [
      {"failure_stage": "diagnosis_schema", "failure_reason": "validation_error", "count": 1},
      {"failure_stage": "evidence_reference", "failure_reason": "invalid_trace_reference", "count": 1}
    ]
  }
}
```

- `count`는 started=true, state=failed, 저장된 request.status=failed, request.error_code=invalid_output인 실행 수다. `rate` 분모는 해당 그룹의 모든 started 실행 수이며 incomplete도 기존 Execution Success 분모와 동일하게 포함한다. 미시작/환경 준비 실패는 제외한다. 분모 0이면 value=null이다.
- 분류는 해당 실행의 `final_result(error_code=invalid_output)`가 정확히 한 건이고 유효한 metadata가 있을 때 그 쌍을 한 번 센다. final_result가 없거나 여러 건, metadata가 없으면 unclassified다. `execution_failed`를 두 번째 표본으로 세지 않는다. Trace 전체가 불완전하더라도 유일한 canonical final_result의 metadata가 있으면 확인된 분류를 사용할 수 있다.
- `classified_count + unclassified_count == count`, `sum(by_reason.count) == classified_count`를 만족한다. `by_reason`은 stage/reason 순으로 정렬하고 0건 항목은 생략한다. 대상 실패 0이면 count/classified/unclassified는 0이고 by_reason=[]다.
- request 종료 결과를 잃은 incomplete 실행은 Trace만으로 최종 invalid_output 분자에 추정 편입하지 않는다. 기존 incomplete·오류 영역·completeness를 유지한다.
- 기존 `failed`·`execution_success`·`correctness`·일곱 Metrics와 Judge 입력은 그대로다. metadata는 모델 또는 Judge에 전달하지 않는다. summary의 추가 진단 내역이지 새 품질 Metric이 아니다.
- summary가 최초 채점 전에는 기존처럼 ungraded placeholder일 수 있다. 기존 수집/채점에서 summarize를 호출하는 지점에 자연스럽게 반영하고, 관측성 때문에 새 채점·LLM 호출을 만들지 않는다.

과거 결과는 읽기만 해도 원본 byte/hash가 보존되어야 한다. 분류 없는 과거 `invalid_output`은 모두 unclassified이고 Token/Case/Mode로 사유를 채우지 않는다. 기존 재채점 준비의 별도 디렉터리·source hash 계약을 사용하며 새 사유를 역으로 채워 넣지 않는다. 실제 기존 50회 결과를 다시 쓰거나 Judge를 재호출하는 작업은 포함하지 않는다.

### D6. API·DB·기존 Consumer의 호환 범위를 명시한다

문의 생성/진단/결과 응답인 RequestDTO와 HTTP 상태·error_code는 그대로다. Trace API는 기존 조직 인증을 통과한 Consumer에 안전한 `failure_metadata`를 추가 반환한다. 여기서 내부 Trace는 기존 애플리케이션 실행 기록이라는 뜻이며 새로운 관리자 전용 API나 권한은 도입하지 않는다.

새 Reader는 필드가 빠진 과거 JSON과 null인 과거 DB 행을 읽는다. 이전 strict Consumer가 새 필드를 거부할 수 있으므로 양방향 완전 호환이라고 주장하지 않는다. 새 결과는 갱신된 Reader로 읽고, nullable 필드 도입을 Runbook에 명시한다. 기존 `llm_metadata` 필드와 그 의미는 유지한다.

기존 migrations 패턴대로 `003_diagnosis_failure_metadata.sql`에 nullable JSONB 열 하나를 추가한다. 자동 적용·데이터 backfill·기존 행 update·event_type constraint 변경은 없다. 저장 코드는 기존 trace.model_dump 경로를 사용한다.

### D7. 실패 분류와 실행 불변성을 함께 검증한다

기존 테스트의 정적 확인 결과 `test_agent_loop.py`는 incomplete·잘못된 JSON·call ID·참조 오류, `test_workflow_runner.py`는 빈 출력·incomplete·function_call, `test_workflow_execution.py`는 실패 원자성과 참조 오류를 검증한다. 이를 재사용하고 빈 타입/필드 경로·metadata 전달·구체 분류·호환/집계를 보강한다.

| 검증 계층 | 검증할 내용 |
|---|---|
| DTO/Runtime | 허용 쌍·path·extra 거부, legacy ExecutionFailure(code), metadata가 예외 메시지에 없음, 과거 Trace 누락→null |
| 공통 LLM + Runner | D3 각 분기, missing/incomplete status, call ID 누락/공백/타입/동일응답·이전턴 중복, Workflow function_call과 빈 text. 실패 뒤 추가 호출 없음 |
| 공통 최종 검증 | 비어 있지 않은 잘못된 JSON과 Schema 구분, 누락/extra/잘못된 root·타입·공백/required_content, 실제 관찰 아닌 sequence, 성공 관찰 없음, 정책 source/검색 결과 불일치, 관찰 구조 KeyError/TypeError |
| 경계 보존 | Budget/refusal 우선순위, 기존 llm_error/tool_error/limit_exceeded/database_error 유지. malformed response 타입이 기존처럼 tool_error임을 확인 |
| Service/API/DB | 양 Mode에서 단계가 다른 실패도 final_result metadata 한 건, execution_failed 기존 형태, diagnosis=null/200/failed 및 재호출409, 별도 Session/API round-trip, 다른 조직404, 종료 저장 rollback/503 |
| Migration | 테스트 소유 schema에서 이전 행·관계·개수·기존 llm_metadata 보존, 새 열 null, 신규 ORM과 같은 구조 |
| Evaluation | Trace JSON round-trip, Case×Mode/Mode 집계·0분모·미분류·중복 final_result·DB 최종 오류·incomplete, 기존 지표 불변, 이전 파일 읽기/별도 재채점 준비의 원본 hash 불변 |
| 성공 회귀·정보 보호 | 같은 Fake script의 요청 input/tools/schema/호출 수와 성공 Diagnosis/이벤트 sequence 동일. 민감정보 표식이 새 필드·로그·오류 응답·평가 산출물에 없음 |

통합 테스트는 기존 격리 PostgreSQL fixture를 사용한다. API Key 없이 Fake Client/합성 예외·고정 시계로 수행하며 실제 환경변수 대신 테스트 값만 주입한다. 실행할 수 없는 DB 테스트는 미실행으로 보고한다. Fake 검증을 실제 실패율 감소·진단 품질 향상으로 보고하지 않는다.

## Risks / Trade-offs

- [원문 없는 분류의 한계] → 분류는 Application이 거부한 정확한 경계다. upstream 원인이나 과거 세 건의 Root Cause를 증명한다고 표현하지 않는다.
- [metadata 생성이 새 예외를 유발할 위험] → 소스에서 고정 DTO 값만 생성하고 loc는 허용 경로로 투영한다. 임의 payload를 metadata로 검증하거나 연결하지 않는다.
- [오류 분류를 나누며 실행을 바꿀 위험] → 기존 검증 순서·수락 집합·JSON Schema 불변, 모든 실패 분기·비대상 오류와 성공 script의 회귀를 검증한다.
- [Trace API의 strict Consumer] → 필드 추가와 새 Reader 필요성을 명시한다. 과거 기록 호환과 이전 Reader의 새 결과 호환을 구분한다.
- [종료 DB 장애 시 metadata 유실] → 기존 원자적 저장·503을 유지한다. 별도 로그/저장소로 우회하지 않고 미기록으로 남긴다.
- [중복/부분 Trace와 평가 과대 집계] → 저장된 최종 오류를 분자 기준으로 사용하고 canonical final_result만 한 번 세며 나머지는 unclassified로 남긴다.

## Migration Plan

1. 후속 apply에서 DTO·계층별 전달·Service·집계·migration·테스트와 Runbook을 함께 구현한다. 이번 propose에서는 문서 외에 변경하지 않는다.
2. 기존 DB에 001/002가 적용된 상태를 전제로 운영자가 새 코드 실행 전에 003을 수동 적용한다. 구현 검증에서는 기존 개발/평가 schema에 실행하지 않고 테스트 소유 schema만 사용한다.
3. 갱신된 Application과 Evaluation Reader를 배포한다. 이전 행은 null로 읽고 새 실패에만 분류를 기록한다. 정상 Trace에는 null 필드 외의 변화가 없다.
4. rollback은 새 열을 남긴 채 이전 Application으로 되돌리는 방식이다. 기존 ORM은 추가 열을 무시한다. 이전 Reader는 새 평가 파일에 쓰지 않고 해당 파일과 새 분류는 보존한다. 열 삭제나 과거 결과 재작성은 rollback 절차에 포함하지 않는다.

## 예정 파일과 구현 경계

확정 수정 대상은 Proposal의 Impact를 따른다. `app/diagnosis/tools.py`, `app/diagnosis/prompts.py`, `app/config.py`, `app/api/support.py`, `evaluation/runner.py`, `evaluation/judge.py`, Dataset/GT/가격표는 동작 변경 대상이 아니다. `evaluation/results.py`는 DTO 전달로 충분하면 변경하지 않는다.

테스트는 `tests/test_diagnosis_failure_observability.py`, `tests/test_diagnosis_failure_storage.py`를 중심으로 추가하고 기존 Agent/Workflow/Schema/Evaluation 테스트를 필요한 만큼 보강한다. 기능 구현은 한 nullable 열·작은 metadata DTO·기존 예외 전달 확장·기존 실패 분기의 분류 부착·summary 그룹 확장으로 제한한다.
