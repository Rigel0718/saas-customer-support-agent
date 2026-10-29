# Proposal

## Why

5개 Case × Agent/Workflow × 5회 반복의 50건 Evaluation에서 `invalid_output` 3건이 발생했지만, 여러 실패 분기가 동일한 오류 코드와 종료 Trace로 합쳐져 실패 단계를 구분할 수 없다. 앞으로의 출력 실패를 안전한 구조화 정보로 식별하고 집계하되, 원문이 없는 과거 세 실행의 Root Cause는 추정하지 않는다.

## What Changes

- 기존 `final_result` Trace에 nullable `failure_metadata`를 추가한다. 실패 지점에서 정한 `failure_stage`, 고정된 `failure_reason`, 안전하게 정규화한 선택적 `field_path`를 보존한다.
- 공통 LLM 응답 상태, Agent Tool call ID, Workflow의 예상하지 않은 Tool 요청, 빈 텍스트, JSON/Schema 및 Evidence/정책 참조 검증을 실제 코드 분기에 따라 분류한다. 기존 `ExecutionFailure`로 전달하고 Service에서 한 번 기록한다.
- 문의 API의 `error_code="invalid_output"`, Fail-fast, 호출·검증 순서, 상태 전이, 권한, 종료 트랜잭션과 실패 시 Diagnosis 미저장을 유지한다. 새로운 실패 이벤트·Retry·Recovery는 추가하지 않는다.
- Evaluation의 기존 Trace 직렬화를 재사용해 새 정보를 보존하고, Mode 및 Case×Mode 집계에 `invalid_output` 비율과 분류별 건수·미분류 건수를 추가한다. 기존 Execution Success·Correctness·호출·Token·비용·Latency 정의는 유지한다.
- 이전 DB/Trace/평가 결과의 누락 필드는 null/미기록으로 취급한다. 신규 nullable JSONB 열에 수동 SQL migration을 제공하며 기존 데이터·평가 원본을 backfill하거나 변경하지 않는다.
- Fake Client·고정 시계·격리 테스트 DB로 실패 분류, 저장/조회, 안전한 필드 경로, 결과 호환·집계 및 기존 성공 경로를 검증한다.

이번 단계는 계획 문서만 작성한다. 구현, 실제 LLM/Judge 호출, Evaluation 재실행, `.env`·Secret 조회는 하지 않는다. C05의 `subscription_expired` 누락 및 Tool 조회 누락, 실행 전략, Prompt/Model/Golden Set/Judge Rubric 변경, 응답 자동 복구, 원문 저장, 전체 Logging Framework·분산 Tracing은 범위에서 제외한다.

## Capabilities

### New Capabilities

- `diagnosis-output-failures`: Agent/Workflow 출력 실패의 공통 분류·전달·안전한 기록과 Evaluation의 분류별 집계 및 미기록 정보 처리.

### Modified Capabilities

- `support-requests`: nullable 출력 실패 메타데이터의 Trace 저장·조회, 이전 데이터 호환 및 기존 원자성·조직 경계 유지 요구사항을 추가한다.

`llm-execution`과 `diagnosis-evaluation`은 각각 미아카이브 Change에서 구현된 현재 계약을 재사용한다. 해당 Change의 delta를 수정하거나 복제하지 않고, 이번 분류·집계 확장은 `diagnosis-output-failures`가 소유한다. `support-requests`에는 별도 ADDED Requirement를 사용해 기존 LLM 메타데이터 delta와 중복 수정을 피한다.

## Impact

- 예정 코드: `app/schemas/support.py`, `app/models/support.py`, `app/diagnosis/runtime.py`, `app/diagnosis/llm.py`, `app/agent/loop.py`, `app/workflow/runner.py`, `app/services/support.py`, `evaluation/metrics.py`. `evaluation/results.py`는 현행 중첩 DTO 직렬화·Reader로 전달 가능한지 검증하고 필요한 최소 변경만 한다.
- 예정 저장/문서: `migrations/003_diagnosis_failure_metadata.sql`, `docs/evaluation_runbook.md`, 관련 신규·기존 테스트.
- Trace API는 nullable 필드 추가다. 문의 응답과 기존 오류 코드는 유지하지만, 미지 필드를 거부하는 이전 Trace/Evaluation Consumer는 새 결과를 읽기 전에 갱신해야 한다. 새 Reader는 이전 결과를 읽고 누락 사유를 추정하지 않는다.
- 새 Endpoint, 환경변수, 의존성, 테이블, 백그라운드 저장소는 도입하지 않는다. 기존 두 미아카이브 Change, main specs, 과거 평가 파일은 이번 propose에서 변경하지 않는다.
