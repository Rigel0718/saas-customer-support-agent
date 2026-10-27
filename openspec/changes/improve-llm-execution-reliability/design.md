# Design

## Context

목적과 범위는 [Proposal](proposal.md), 외부 동작은 [LLM 실행 Spec](specs/llm-execution/spec.md) 및 [Trace delta](specs/support-requests/spec.md)를 따른다. 이번 문서는 설계안이며 구현·DB 변경·회귀 테스트 실행은 하지 않는다.

| 현재 경계 | 확인한 동작과 설계 영향 |
|---|---|
| `app/diagnosis/llm.py` | `call_model`이 `min(20, budget.remaining())`을 SDK 요청에 전달한다. `responses_create`는 전체 남은 Budget으로 별도 `asyncio.timeout`을 적용하고 SDK 재시도를 끈다. `call_model`의 예외 처리에서 원본 예외를 버리므로 이 경계에서 안전한 분류를 추출한다. |
| `app/config.py` | Agent/Workflow 실행 Budget은 각각 기본 60초이며 `Settings.from_env()`가 환경변수를 읽는다. 요청 Timeout 설정은 없다. |
| `scripts/evaluate.py` | `diagnosis_settings()`가 모델·키만 읽어 기본 `Settings`를 생성하여 Budget 환경변수가 평가에 반영되지 않는다. Runner가 받는 동일 객체를 manifest 생성에도 사용한다. |
| Trace 저장 | `TraceDTO` 필드를 `save_terminal`이 ORM으로 전달한다. Trace의 observation은 Tool 결과와 Diagnosis 근거에 쓰이므로 LLM 오류를 여기에 혼합하지 않는다. |
| 결과 호환 | `Manifest.configuration`은 dict이며 JSON 버전은 `evaluation-v1`이다. 이전 Trace에는 새 필드가 없으므로 nullable 기본값으로 읽을 수 있어야 한다. |
| 기존 검증 기반 | `test_agent_loop.py`, `test_workflow_deadline.py`, `test_diagnosis_runtime.py`, `test_evaluation_cli.py`, `test_evaluation_results.py` 등에 Fake 주입·시계·저장 검증 패턴이 있다. `001_workflow_execution_mode.sql`과 migration 테스트 패턴을 재사용한다. |

Pilot 분석의 확정 범위는 공통 코드의 20초 설정과 20,063ms/20,067ms의 실패 관찰이다. 원본 예외가 없으므로 Timeout이었다고 확정하지 않는다. 이번 설계는 후속 실행에서 이 구분을 가능하게 하며 C04 Agent의 탐색 소진이나 C05의 원인 누락을 해결하지 않는다.

`agent-workflow-evaluation`은 코드 구현이 완료된 미아카이브 Change다. 해당 계획 문서를 소급 수정하지 않고 현재 구현 위에 별도 Change로 추가한다. main에는 아직 `diagnosis-evaluation`이 없으므로 중복 ADDED/MODIFIED delta를 만들지 않는다. 그 평가 계약을 소비하는 새로운 Timeout 요구는 `llm-execution`에서 정의한다.

## Goals / Non-Goals

**Goals:** 공통 호출 함수·Settings·기존 Trace/manifest를 사용하여 실패 분류와 실제 Timeout을 추적한다. 설정 해석과 분류에 작은 함수만 추가하고 기존 실행·저장 경계를 유지한다.

**Non-Goals:** 새 LLM Client 계층, 오류 프레임워크, Retry/Backoff, 별도 관측 서버·테이블·파일, Prompt/Tool/진단 변경, Judge Timeout 통합, DB Timeout 변경 및 실제 안정성 개선 측정은 포함하지 않는다. Non-Goals의 전체 목록은 Proposal을 따른다.

## Decisions

### D1. 기존 Trace에 명시적 nullable `llm_metadata` 추가

`TraceDTO`와 `AgentTrace`에 필드 하나를 추가한다. DTO에서는 작은 Pydantic 모델 두 개로 허용 필드와 타입을 제한하고, ORM에서는 nullable JSONB를 사용한다. 기존 `trace.model_dump()` 기반 종료 저장 흐름을 그대로 이용하며 JSON 값을 별도 저장소에 복제하지 않는다.

```json
{
  "request_timeout_seconds": 35.0,
  "error": {
    "exception_type": "APITimeoutError",
    "is_timeout": true,
    "http_status": null
  }
}
```

- `request_timeout_seconds`: 그 호출에서 SDK와 요청에 전달한 실제 값. 설정 상한과 남은 Budget 중 작은 값이다.
- `error`: `llm_error` 예외 처리 시에만 생성한다. 정상 응답 및 기존 별도 오류(`model_refusal`, `invalid_output`, `execution_timeout`)는 null이다. 전체 Budget 오류 분류까지 확대하지 않아 현재 변환 경계를 유지한다.
- 변경 이후 `llm_response` 이벤트는 성공·실패 모두 요청 Timeout을 기록한다. 호출 이전에 Budget이 이미 소진되어 LLM 시도가 없으면 새 LLM 이벤트를 만들지 않는다.
- LLM 이외의 이벤트와 이전 Trace는 `llm_metadata=null`이다. 모델에는 이 메타데이터를 전달하지 않는다.

대안인 Tool observation 재사용은 근거 관찰과 실행 진단 정보를 혼동하게 한다. 별도 로그/파일은 DB Trace와 Evaluation에서 함께 조회하기 어렵다. JSONB 한 열을 추가하면 기존 원자적 저장과 결과 수집 경로를 그대로 쓸 수 있다.

### D2. 원문을 읽지 않는 허용 목록 기반 분류

분류는 `call_model`의 현재 예외 처리에서 기존 최종 오류 코드를 결정한 뒤 수행한다. `llm_error`인 경우에만 `llm.py`의 작은 함수가 고정된 값 세 개를 반환한다. 예외 객체는 반환·저장하지 않는다.

| 관측 가능한 예외 | exception_type | is_timeout | http_status |
|---|---|---|---|
| OpenAI `APITimeoutError` | `APITimeoutError` | true | null |
| OpenAI `APIConnectionError` | `APIConnectionError` | false | null |
| OpenAI `APIStatusError`와 알려진 하위 타입 | 허용 목록의 해당 타입, 미등록 하위 타입은 `APIStatusError` | false | 검증한 정수 또는 null |
| Python `TimeoutError` | `TimeoutError` | true | null |
| 기존 내부 `ExecutionFailure`의 llm_error | `ExecutionFailure` | false | null |
| 그 외 예외 | `UnknownError` | false | null |

HTTP 하위 타입 허용 목록은 `BadRequestError`, `AuthenticationError`, `PermissionDeniedError`, `NotFoundError`, `ConflictError`, `UnprocessableEntityError`, `RateLimitError`, `InternalServerError`로 한정한다. 구체적인 타입부터 판정하여 Timeout이 일반 연결 오류로 분류되지 않게 한다. HTTP Status는 알려진 HTTP 오류의 `status_code`만 사용하고 bool이 아닌 int이며 100~599일 때만 보존한다.

클래스 이름을 `type(exc).__name__`으로 출력하지 않고 알려진 클래스를 고정 문자열로 매핑한다. 메시지·repr·args·request·response·Header·URL·body·stack과 `__cause__`/`__context__` 체인을 읽거나 직렬화하지 않는다. `is_timeout=false`는 알려진 Timeout을 감지하지 않았다는 뜻이며 미지의 예외가 Timeout이 아니라고 증명하지 않는다. 누락 설정은 현행 `ExecutionFailure('llm_error')` 경로를 유지한다.

대안인 전체 예외 로깅 후 마스킹은 본문·Credential 누출 경로를 넓힌다. 공급자 오류 체인의 상세 분석은 이번 요구에 필요하지 않다. 알려진 타입과 정수 Status만으로 Pilot에 없던 구분을 제공한다.

### D3. 요청 Timeout과 전체 Budget을 분리하여 유지

`Settings` 마지막에 `llm_request_timeout_seconds: float = 20`을 추가하여 기존 positional 인자 순서를 보존한다. `Settings.from_env()`에서 `LLM_REQUEST_TIMEOUT_SECONDS`를 읽고 기존 Budget 두 개와 함께 양의 유한 수를 검증한다. Timeout 환경변수 파싱은 API와 Evaluation이 공유할 작은 함수로 모으되 전체 Settings 생성·인증·DB 설정을 통합하지 않는다.

`call_model`의 상수 20만 설정값으로 바꾼다. 계산한 `min(settings.llm_request_timeout_seconds, budget.remaining())`을 한 번 보관하여 SDK 옵션과 Trace에 동일하게 사용한다. `responses_create`의 전체 Budget 기반 외부 취소, SDK `max_retries=0`, 호출별 Client 수명은 유지한다.

SDK 요청 Timeout은 HTTP I/O Timeout 설정이고 완전한 요청 wall-clock 상한과 동일하지 않다. 전체 Budget 취소가 별도로 존재한다. 전체 Budget 소진 시 `execution_timeout`, 그 전에 SDK Timeout 예외가 발생하면 `llm_error`라는 기존 우선순위는 바꾸지 않는다. 요청 상한이 Budget보다 큰 값도 허용하되 남은 Budget으로 줄인다.

기본값을 임의로 늘리는 대안은 Pilot의 미확정 원인을 해결한다고 볼 수 없고 비교 기준도 바꾼다. 기본 20/60초를 유지하고 후속 평가자가 값을 명시하여 새 run으로 측정하게 한다.

### D4. Evaluation의 명시 옵션과 환경변수 우선순위

`run`에 다음 옵션 두 개를 추가한다. `plan`은 현재의 외부 호출 없는 Dataset 계획 기능을 유지하고 `judge`/`calibrate`에는 이 옵션을 추가하지 않는다.

| 옵션/설정 | 해석 순서 | 최종 적용 |
|---|---|---|
| 요청 Timeout | `--llm-request-timeout-seconds` → `LLM_REQUEST_TIMEOUT_SECONDS` → 20 | 두 모드 공통 |
| 실행 Budget | `--execution-timeout-seconds` → `AGENT_TIMEOUT_SECONDS`, `WORKFLOW_TIMEOUT_SECONDS` 각 값 → 각각 60 | 두 모드 값이 같아야 평가 허용 |

CLI 옵션 기본값은 미지정 상태로 두어 환경변수를 덮어쓰지 않는다. 명시 옵션이 있으면 해당 하위 우선순위 환경변수를 파싱하지 않는다. 특히 공통 실행 Budget 옵션이 있으면 두 환경변수 값이 다르거나 잘못됐어도 명시한 공통값을 사용한다. 선택된 값이 빈 문자열·잘못된 숫자·0·음수·NaN·무한대이면 안전하게 거부하며 입력값 자체를 오류 메시지에 포함하지 않는다.

`diagnosis_settings`가 해석한 값을 `Settings`에 전달한다. 기존 Evaluation DB URL 검증과 실제 호출 허용 옵션은 유지한다. `Settings.from_env()` 전체를 호출하면 불필요한 Bearer 설정에 의존하므로 사용하지 않는다. Agent step/Tool 한도는 기존 평가 기본값을 유지한다.

해석·유효성·두 모드 Budget 일치 검사는 DB 연결·결과 디렉터리 생성 전에 수행한다. API에서는 기존처럼 모드별 Budget이 달라도 허용한다. 평가에서는 공통 조건을 유지하기 위해 불일치를 거부하고, 메시지에 공통 Budget 옵션으로 맞출 수 있음을 안내한다. 새로운 비교 설정을 허용하면서 예전 평가 문서의 고정 기본값을 소급 수정하지 않는다.

### D5. manifest 설정값과 Trace 적용값의 구분

기존 `Manifest.configuration`에 `llm_request_timeout_seconds`를 추가하고 기존 `agent_timeout_seconds`·`workflow_timeout_seconds`는 실제 해석값으로 기록한다. manifest를 만드는 Settings와 Runner에 전달하는 Settings는 같은 객체다. 전체 설정 객체·환경변수·CLI 인자 전체를 직렬화하지 않는다.

설정 40초라도 뒤쪽 Agent 호출은 Budget이 6초 남으면 Trace에 6초를 남긴다. 정상 응답, 요청 실패 모두 이를 보존하여 설정 의도와 실제 제약을 구분한다. 오류 호출의 usage/model 부재를 채우지 않으며 기존 Metrics 계산은 유지한다.

`evaluation-v1`은 additive 필드 변경으로 유지한다. 새 Reader는 이전 Trace 필드 누락을 null로 읽고, manifest에 없는 요청 Timeout을 기본값으로 채우지 않는다. 실제 Pilot 원본은 수정하거나 정규화 저장하지 않는다. 이전 데이터 호환 테스트는 비밀 없는 합성 v1 파일을 임시 디렉터리에 생성하여 수행한다. 재채점 파생 파일에 nullable 필드가 나타나더라도 원본 파일 및 source hash는 유지하고 과거 Timeout을 추정하지 않는다.

버전 전체를 올리면 이번 additive 변경에 불필요한 Reader/재채점 분기가 생긴다. 단, 이전 Reader는 새 Trace 필드를 거부할 수 있으므로 새 결과는 변경 이후 Reader로 읽는다는 배포 조건을 문서에 남긴다.

### D6. 검증 경계

- 공통 호출 단위 테스트: Fake `create`와 합성 OpenAI 오류(Timeout, 연결, HTTP 401/429/500), 일반 TimeoutError·미분류 예외, 타입·Status 검증, 민감정보 표식 누출 방지, LLM 시도당 이벤트 한 건.
- 고정 시계: 설정 35/잔여 50이면 35, 설정 35/잔여 7이면 7; Budget 소진 시 기존 코드·후속 호출 중단 유지. Fake Async Client로 `responses_create`의 SDK/요청 Timeout 전달과 재시도 0회를 확인한다.
- 설정/CLI: 기본값·환경변수·명시 옵션·우선순위·잘못된 값·모드 불일치를 검증한다. Fake run 경로가 실제로 받는 Settings와 생성된 manifest 값, 진단 Fake Client에 전달된 Timeout까지 연결하여 검사한다.
- 저장/API: 기존 격리 PostgreSQL 패턴과 Fake LLM으로 두 모드의 성공·실패 Trace를 별도 세션에서 재조회한다. 새 열 migration의 이전 행 null·기존 관계 보존을 검사한다.
- 결과: 새 메타데이터 round-trip, 합성 이전 v1 로드/재채점 준비·원본 hash 불변, 기존 Metrics·Diagnosis/Prompt/조회 순서 회귀를 검사한다.

추가 테스트는 실제 API Key·OpenAI 호출을 사용하지 않는다. DB가 필요한 검증은 기존 테스트 소유 schema만 사용하고 `.env`·Secret 파일을 읽거나 출력하지 않는다. 이번 propose에서는 OpenSpec 문서 검증만 수행하며 구현 테스트는 후속 apply 작업이다.

## Risks / Trade-offs

- [더 긴 Timeout은 대기·비용 증가 가능] → 기본값 유지, 전체 Budget·재시도 0회 유지, 새 run에서만 변경값 측정.
- [분류로 공급자 내부 원인까지 알 수 없음] → 관측한 예외 타입·Status만 기록하고 응답 지연이나 추론 복잡성을 확정하지 않음.
- [미분류 예외 상세 손실] → `UnknownError`로 안전하게 보존하며 새 타입 필요 시 명시적 허용 목록과 Fake 검증으로 추가.
- [새 JSONB 열이 없는 기존 DB에서 실행 불가] → 배포 전 수동 migration 안내와 이전 데이터 보존 테스트.
- [과거 결과에 진단 정보가 없음] → null/미기록 유지, 소급 추정·원본 수정 금지.
- [관측성 개선이 Diagnosis Correctness 개선으로 오해됨] → C04 탐색·C05 완전성은 후속 별도 Change이며 이번 완료 기준에 포함하지 않음.

## Migration Plan

1. 구현 시 `002_llm_trace_metadata.sql`에 nullable `agent_traces.llm_metadata JSONB` 열을 추가하는 트랜잭션을 작성한다. 데이터 backfill과 자동 시작 migration은 수행하지 않는다.
2. 기존 DB 운영자는 새 코드 사용 전에 소유 DB에 수동 적용한다. 테스트/Evaluation 신규 schema는 갱신된 ORM 초기화로 동일 열을 생성한다. 과거 Pilot 전용 schema에는 이번 작업으로 migration을 실행하지 않는다.
3. 새 코드와 Reader를 함께 사용하고 기존 Runbook에 설정 우선순위, 20초 요청/60초 Budget 구분, 수동 migration과 적용값 확인 예시를 추가한다. `.env.example`은 기존 사용자 변경을 보존하며 설정 이름/기본값만 보완한다.
4. 새 run에서만 설정과 메타데이터를 기록한다. 기존 `.evaluation-results/invite-10` 등 Pilot 파일은 읽기 전용으로 둔다.
5. 애플리케이션 rollback 시 새 nullable 열을 그대로 두어 기존 코드와 새 기록을 보존한다. 이번 Change에는 열 삭제나 데이터 삭제 자동화가 필요하지 않다.
