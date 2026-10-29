# Tasks

아래는 후속 apply의 미완료 구현 작업이다. 이번 propose에서는 계획 문서만 작성하며 코드·DB·환경변수·기존 평가 결과를 변경하지 않는다. 완료 기준은 [Design](design.md)과 [출력 실패](specs/diagnosis-output-failures/spec.md)·[저장/조회](specs/support-requests/spec.md) 명세를 따른다.

## 1. 공통 Failure Metadata와 전달 계약

- [x] 1.1 `app/schemas/support.py`에 Design D3/D4의 고정 stage/reason 쌍과 nullable field_path만 허용하는 작은 metadata DTO를 추가하고 `TraceDTO.failure_metadata=None`을 연결한다. DTO 단위 테스트로 허용 쌍·알 수 없는 쌍/extra 거부·허용 path·임의 key/bool/음수 인덱스 차단·이전 Trace 누락→null 및 기존 Diagnosis JSON Schema 불변을 검증한다.
- [x] 1.2 `ExecutionFailure`에 keyword-only optional failure_metadata 전달을 추가한다. `tests/test_diagnosis_failure_observability.py`에서 기존 `ExecutionFailure(code)` 호출·code/str 값 유지, 분류 전달 및 metadata/민감정보가 예외 문자열에 없음을 검증한다.
- [x] 1.3 `docs/evaluation_runbook.md`에 metadata 필드·canonical final_result·내부 Trace의 기존 조직 권한 의미와 원문 미수집 원칙을 추가한다. 예시가 DTO 허용 필드만 포함하고 문의 error_code 계약을 변경하지 않음을 문서·DTO 테스트로 대조한다.

## 2. 공통 LLM 및 Mode별 실패 분류

- [x] 2.1 `call_model()`의 기존 non-completed 분기에 response_status/not_completed를 부착한다. Fake 응답의 missing/incomplete status와 Budget/refusal 복합 조건으로 기존 검사 순서·llm_response 한 건·error_code/llm_metadata 유지·실패 뒤 추가 호출 없음을 검증한다.
- [x] 2.2 `run_agent()`의 call ID 거부를 invalid_call_id/duplicate_call_id로 분류한다. 누락/null/공백/비문자열·같은 응답/이전 턴 중복·형식+중복 복합 사례를 테스트하고 Tool 실행 차단 시점·기존 정상 호출과 입력을 유지함을 확인한다.
- [x] 2.3 `run_workflow()`의 function_call/빈 문자열 분기에 각각 unexpected_function_call/empty_output을 전달한다. Fake 테스트로 분류·우선순위·일곱 조회/단일 LLM·추가 호출 0회 및 기존 malformed response 타입의 tool_error 매핑을 검증한다.
- [x] 2.4 Runbook에 응답 상태·Mode별 taxonomy와 llm_response가 정상 최종 출력을 보장하지 않는다는 해석을 작성한다. 분류 예시를 2.1~2.3의 실제 Fake Trace와 대조하고 Token 수로 원인을 추정하는 설명이 없음을 확인한다.

## 3. 공통 JSON·Schema·Evidence 분류와 정보 보호

- [x] 3.1 `validate_diagnosis()`에서 Agent의 빈 최종 문자열을 empty_output으로 분류하고, 기존 Pydantic JSON 검증의 json_invalid와 나머지 validation_error를 분리한다. Fake/직접 단위 테스트로 빈/공백·잘린 JSON·잘못된 root·필드 누락/extra·타입·문장 공백·required_content 오류와 기존 성공 결과 불변을 검증한다.
- [x] 3.2 Pydantic 첫 오류의 loc를 Design D4의 고정 경로로 정규화한다. 합성 민감정보를 추가 key·입력·ctx에 넣은 테스트로 임의 경로→null, root validator→null, 유효 경로 보존 및 metadata·예외/오류 응답·캡처 로그에 input/ctx/msg/원문 표식이 없음을 검증한다.
- [x] 3.3 기존 관찰/정책 검증 분기에 invalid_trace_reference/no_successful_observation/invalid_policy_reference/unobserved_policy_document/invalid_observation을 부착한다. 실제 Trace sequence와 step 혼동, 다른 검색에만 있는 정책 ID, 실패 관찰, KeyError/TypeError를 테스트하고 첫 실패 우선순위·Diagnosis 허용/거부 조건·추가 호출 0회를 검증한다.
- [x] 3.4 Runbook에 JSON/Schema/참조 taxonomy와 안전한 field_path 예시, 거부 단계 식별과 원문 재현의 차이를 추가한다. D3의 모든 허용 쌍이 문서·파라미터화 테스트에 대응하고 Prompt/Diagnosis validator를 수정하지 않았음을 diff로 확인한다.

## 4. Service 종료 기록·DB 저장·Trace API

- [x] 4.1 `diagnose_request()`의 검증 직후 및 바깥 ExecutionFailure 경계에서 metadata를 기존 final_result 한 건으로 전달한다. 양 Mode의 조기/최종 실패 Fake 통합 테스트로 final_result 중복 없음·execution_failed metadata=null·failed/diagnosis=null/invalid_output/200·재호출409와 기존 sequence를 검증한다.
- [x] 4.2 `AgentTrace.failure_metadata` JSONB 및 `migrations/003_diagnosis_failure_metadata.sql`을 추가한다. `tests/test_diagnosis_failure_storage.py`에서 테스트 소유 schema의 이전 문의·진단·Trace·llm_metadata·관계·개수 유지와 새 열 null, 신규 ORM 구조 일치를 검증한다. 기존 개발/평가 schema에는 migration을 실행하지 않는다.
- [x] 4.3 기존 trace.model_dump 저장 및 Trace API 경로의 round-trip을 검증한다. 별도 Session/API의 metadata 일치·다른 조직404/미인증401·metadata가 근거/모델/Judge 입력에 없음·종료 rollback/503·DB 실패로 최종 오류가 바뀌어도 기존 실패 복구 저장 정책 유지를 테스트한다.
- [x] 4.4 Runbook에 001/002 선행·003 수동 적용·추가 열을 남기는 rollback, nullable 필드 추가와 strict Consumer 갱신 범위를 작성한다. SQL에 backfill/기존 행 update/자동 적용이 없고 과거 JSON 누락→null 및 기존 llm_metadata 의미 유지 테스트와 문서가 일치함을 확인한다.

## 5. Evaluation 보존·집계·이전 결과 호환

- [x] 5.1 기존 `Execution.traces` 및 ResultStore의 중첩 DTO 경로로 새 metadata가 보존되는지 검증하고 필요한 경우에만 `evaluation/results.py`를 최소 수정한다. 합성 신규/이전 evaluation-v1 파일 round-trip과 별도 재채점 준비 테스트로 metadata 값·기존 측정값·원본 byte/hash·source 연결 유지 및 진단/Judge 호출 0회를 확인한다.
- [x] 5.2 `evaluation/metrics.py`의 기존 Mode·Case×Mode 그룹에 Design D5의 invalid_output 블록을 추가한다. 합성 테스트로 저장된 최종 문의 오류에 따른 분자, 모든 started 분모, incomplete/환경 오류/DB 최종 오류 제외 규칙, 0분모와 0실패, canonical final_result 중복/유실·null metadata·부분 Trace 및 두 합계 불변식을 검증한다.
- [x] 5.3 기존 Evaluation 집계 테스트에 새 블록을 제외한 지표 동일성을 추가한다. 두 Mode Fake Service→Trace→executions→summary 흐름에서 metadata가 실행당 한 번 집계되고 LLM/Tool Calls·usage·비용·Latency 경계·Judge 입력이 바뀌지 않음을 검증한다.
- [x] 5.4 Runbook에 출력 실패율 분모·by_reason 정렬/형태·classified/unclassified·과거 원문 복원 불가·새 Reader/이전 strict Reader의 호환 범위와 ungraded placeholder 해석을 작성한다. 합성 summary 및 과거 필드 누락 예시를 테스트 결과와 대조하고 실제 기존 평가 파일은 수정하지 않는다.

## 6. 통합 완료 확인

- [x] 6.1 신규 관측성/저장 테스트와 기존 Agent/Workflow·deadline·LLM metadata·Support API/저장·Evaluation 회귀를 실제 API 없이 실행한다. 준비된 격리 테스트 DB에서 `uv run pytest`로 완료 조건을 확인하고 사용할 수 없는 환경의 항목은 미실행으로 보고한다. `.env`·Secret은 조회하지 않는다.
- [x] 6.2 `openspec validate diagnosis-failure-observability --strict` 및 최종 diff로 명세·테스트 대응과 범위를 확인한다. Prompt/Model/Golden Set/Judge Rubric/Tool 전략·환경변수·기존 Change·평가 원본 불변, 정상 호출/Diagnosis 유지 및 미해결 문제를 한국어로 보고하고 Fake 검증을 실제 실패율/품질 개선으로 주장하지 않는다.

## Workflow follow-up

- 구현은 별도 apply 요청에서 시작한다. 이번 propose는 모든 구현 checkbox를 미완료로 남긴다.
- 실제 LLM/Judge 호출·새 Evaluation은 사용자의 별도 명시 요청이 있을 때만 진행한다. 과거 세 건의 사유를 추정 backfill하지 않는다.
- 구현 검토 후 별도 요청으로 sync/archive한다. 두 기존 미아카이브 Change와의 capability 소유·동기화 순서를 확인하고 해당 문서를 임의 수정하지 않는다.

## 구현 검증 기록

- 2026-10-28: 실행 가능한 회귀 범위 360 passed, 312 deselected. 저장소 테스트는 변경하지 않고 `/tmp`의 임시 pytest collection plugin으로 DB fixture와 소켓 필수 항목만 제외했다. 그 전 실행에서는 360 passed, 로컬 소켓 생성 권한 오류로 22 failed, DB/deadline 등 290 deselected였다.
- 신규 PostgreSQL 저장/API 테스트 13건을 수집했다. 별도 실행은 첫 테스트의 DB 연결 준비 단계에서 중단됐다. DB 접근과 Docker 소켓 사용이 제한되어 4.2/4.3의 실제 migration·별도 Session/API·rollback 검증은 완료하지 않았고 checkbox를 미완료로 유지한다. 코드·SQL·테스트 작성은 완료했다.
- OpenSpec strict 검증과 diff whitespace 검사를 통과했다. 실제 LLM/Judge 호출·Evaluation 재실행·개발/평가 DB migration은 수행하지 않았다. 환경파일은 테스트 프로세스 로딩에만 사용하고 내용을 조회하거나 출력하지 않았다.
- 2026-10-28 사용자 로컬 검증 결과: `uv run --env-file .env pytest tests/test_diagnosis_failure_storage.py -q` → 13 passed, 1 warning; `uv run --env-file .env pytest -q` → 672 passed, 1 warning. 전체 테스트 실패 0건. 테스트 코드와 완료 조건을 대조해 4.2/4.3을 완료 처리했다. 이 결과는 위 DB 접근 제한으로 인한 미완료 상태를 해소하며, 이번 확인 작업에서는 테스트를 재실행하지 않았다.
