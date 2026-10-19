# SaaS Customer Support Agent — support-agent 독립 리뷰

검토일: 2026-10-15. 대상: `feat/issue2-support-agent`의 HEAD `93bf1e257e7e8e2adaa635676fdd326a1ebc9bba`와 현재 작업 트리. 코드·명세·테스트를 수정하지 않고 이 문서만 작성했다. 기존 완료 보고를 검증 근거로 대신하지 않고 파일, 호출 경로, 전체 테스트 및 추가 재현을 확인했다.

## 1. Executive Summary

**최종 판정: READY WITH MINOR FIXES.**

인증된 조직 범위의 문의 접수, Single Agent의 후속 Tool 선택, 관찰·근거 연결, 중복 실행 차단 및 종료 저장은 현재 MVP에 필요한 구조로 구현되어 있다. 고객 Service를 그대로 재사용하며, 모델이 CallerContext나 Session을 교체할 경로 또는 고객 데이터를 수정할 Tool은 확인하지 못했다. 독립 PostgreSQL 연결을 사용하는 저장·동시성 테스트도 실제 Application 코드를 실행한다.

전체 테스트는 **176 passed, 1 warning**, OpenSpec strict 검증은 통과했다. 다만 통과한 테스트가 놓친 **P2 Finding 두 건**을 재현했다.

| ID | Severity | 확인된 문제 |
|---|---|---|
| SA-01 | P2 | SDK 호출이 진행되는 동안 전체 실행 deadline을 강제하지 못한다. 느리게 계속 도착하는 HTTP 본문은 설정 시간보다 오래 기다린다. |
| SA-02 | P2 | 문의 상태와 Diagnosis를 별도 SELECT로 읽어, 종료 commit과 경합하면 GET이 `running`과 non-null diagnosis를 함께 반환한다. |

P0/P1/P3 Finding은 없다. 두 문제는 특정 대기·동시성 조건의 신뢰성 및 응답 계약 문제이며, 재현에서 다른 조직 데이터 노출이나 저장 데이터 손상을 확인하지 않았다. 현재 구조로 Workflow Baseline 구현을 시작할 수 있다. SA-02는 지원 API 보완으로 처리하고, SA-01은 Evaluation에서 설정 시간을 강제된 실행 상한으로 사용하기 전에 수정해야 한다. 현재 상태를 모든 MUST의 완전 충족으로 표현해서는 안 된다.

실제 OpenAI API는 **호출하지 않았다**. OPENAI_API_KEY와 OPENAI_MODEL이 모두 없으며, 실제 모델의 Tool 선택·진단 품질·계정 접근·선택 모델의 기능 호환성은 미검증이다.

## 2. Review Scope & Verification

### 변경 범위와 비교 기준

`git status --short`, `git diff`, staged diff, `git log`, 해당 커밋의 `git show --stat`을 확인했다.

- `d361dfdd20d99ea6cc25170be76c0986f950a1b0`은 `chore: complete backend foundation` 커밋이며, backend-foundation Change archive와 main spec 동기화를 포함한다. archive의 tasks도 모두 완료되어 있다. 이 내용으로 비교 기준을 선정했으며 커밋명을 추측하지 않았다.
- HEAD `93bf1e2`는 support-agent proposal/design/세 spec/tasks와 설계 요약을 추가한 계획 커밋이다. 현재 이력에서 support-agent 구현 커밋은 확인되지 않았다.
- 구현은 미커밋 작업 트리에 있다. 수정된 tracked 파일 13개에 더해 `app/agent/`, 지원 API·모델·Schema·Service, `policies/`, 신규 테스트 등 미추적 파일을 실제로 읽었다. `git diff`에 나타나지 않는 미추적 구현도 리뷰 대상이다. staged 변경은 없다.
- 비교 기준 이후 `app/services/customer.py`, `app/schemas/customer.py`, `app/seed.py`, `tests/test_backend.py`, `compose.yaml`의 변경은 없다. 고객 모델의 변경은 공통 Base import 분리다. DB 엔진에는 공통 timeout 설정이 추가되었다.

요청된 `docs/mvp.md`, `docs/support-agent-design.md`, support-agent의 proposal/design/모든 capability spec/tasks를 읽었다. 기존 customer-data/customer-read-access main spec, backend-foundation 완료 기록 및 Architecture Review의 통합 제약도 참고했다.

### 실행한 검증

| 검증 | 실제 결과 / 한계 |
|---|---|
| `uv run pytest -q` | 전용 PostgreSQL `support_test`, localhost:55433에서 **176 passed, 1 warning in 7.47s**. fixture가 임의 schema를 생성·제거한다. |
| `openspec validate support-agent --strict` | `Change 'support-agent' is valid`. 명세 형식 검증이며 구현 적합성을 자동 증명하지는 않는다. |
| 설치 SDK와 schema 확인 | OpenAI **2.54.0**, HTTPX **0.28.1**. 사용한 Responses 인자가 설치 시그니처에 있으며, Tool 및 최종 JSON Schema의 required/additionalProperties를 확인했다. |
| 동시 GET 추가 재현 | 별도 임시 PostgreSQL schema, 두 Session 및 실제 TestClient 경로에서 `running + diagnosis != null + finished_at == null`을 확인했다. 이후 재조회는 정상 completed다. |
| SDK의 느린 HTTP 수신 추가 재현 | localhost 가짜 HTTP 서버와 실제 SDK·HTTPX transport를 사용했다. 실행 budget 0.2초, 본문을 약 0.08초 간격으로 분할 전송했을 때, SDK 준비 후에도 약 **0.761초** 뒤에야 execution_timeout을 판정했다. |
| 실제 Responses API Smoke | **미실행**. 실제 Key/모델 설정이 없다. localhost 재현의 Key와 모델은 가짜 값이며 OpenAI 서버나 유료 모델을 호출하지 않았다. |

warning은 기존 Starlette TestClient의 HTTPX 사용 deprecation 경고다. 기능 실패로 분류하지 않았다. 추가 재현은 파일에 테스트를 추가하지 않고 일회성 Python 프로세스의 임시 주입으로 실행했다. 동시 GET에서는 SELECT 사이에 종료 commit이 일어나도록 스케줄을 제어했으며, 실제 반환 데이터나 SQL 결과를 가짜 값으로 대체하지 않았다. 재현 schema는 제거했다.

Responses 연결 계약은 OpenAI Docs의 [Function calling](https://developers.openai.com/api/docs/guides/function-calling)과 [Reasoning models](https://developers.openai.com/api/docs/guides/reasoning)를 대조했다. `call_id` 대응, strict object schema 및 후속 reasoning 전달은 문서 계약에 맞는다. 실제 선택 모델의 지원 여부는 이 확인과 별개다.

## 3. Spec Compliance

아래의 충족 표시는 코드 경로와 자동 검증에 대한 판단이다. 실제 모델이 올바른 원인을 판단한다는 의미가 아니다.

### support-requests

| MUST 요구사항 | 구현·검증 근거 | 판정 |
|---|---|---|
| content-only 접수, trim/1~4000자/추가 필드 거부, 201 pending과 nullable 필드 | RequestCreate, `support.create_request`, API 입력·필드 테스트 및 독립 Session 재조회 | 충족 |
| pending→running→completed/failed, 한 번 실행, running/failed 409, completed 재사용 | 조직/pending 조건 UPDATE와 시작 commit; concurrent claim, 실패 재호출 및 completed 무호출 테스트 | 충족 |
| 문의 결과·sequence 순 Trace 및 null 필드, 실행 중 running/null diagnosis | RequestDTO/TraceDTO 및 조회 Service. 실행 전·중·후 테스트는 있으나 SELECT 사이 경합은 누락 | **부분 충족: SA-02** |
| Bearer 인증과 동일 조직 역할별 접근, 교차 조직/미존재 동일 404 | 공통 dependencies와 범위 WHERE, 고객·지원 API 및 Tool 조직 경계 테스트 | 충족 |
| FK/enum/유일성/양수 sequence, Router와 무관한 일곱 테이블 등록 | PostgreSQL 제약 테스트, Base 호환성 및 새 프로세스의 모델 등록 검사 | 충족 |
| 생성·claim commit, 종료 원자 저장, rollback/503와 실패 저장 한 번 | Application의 명시적 commit/rollback, 독립 연결의 flush/commit/read/지속 장애/불명확 commit 검증 | 검증한 경로 충족 |

### support-diagnosis

| MUST 요구사항 | 구현·검증 근거 | 판정 |
|---|---|---|
| 모델이 Tool과 후속 행동 선택, 동일 실행 관찰 전달, 다중 호출 순차 처리 | `run_agent`가 response.output과 call_id별 function_call_output을 누적. scripted fake가 실제 관찰과 순서를 검사 | 충족 |
| 다섯 Tool allowlist, UUID/query/추가 인자 검증과 안전한 잘못된 호출 관찰 | ARGUMENTS/strict Schema/Pydantic 및 직접 Tool 검증. 잘못된 인자는 고객 조회 Service를 호출하지 않음 | 충족 |
| 신뢰된 CallerContext/Session 주입, 생성자 기본 대상 및 조직 경계 | HTTP 인증→Application→Tool→기존 customer Service. admin이 member 문의를 실행하는 E2E 검사 | 충족 |
| not_found·빈 결과·업무 상태와 치명적 실행 오류 구분 | ToolResult 또는 AgentFailure/SQLAlchemyError 분기, 부재·업무 outcome·치명적 오류 검사 | 충족 |
| 구조화된 결과와 같은 실행의 실제 관찰/정책 근거 검증 | DiagnosisDTO의 outcome별 내용 검사와 Application `validate_diagnosis`. 성공 관찰, 정책 ID 및 trace 참조 검증 | 충족 |
| 8회 모델/16회 Tool/60초 기본 한도, 설정 가능, SDK 재시도 금지 | 호출 수와 반환 후 deadline 검사는 구현·테스트됨. SDK 호출 내부의 전체 시간 한도는 미강제 | **부분 충족: SA-01** |
| 시작/LLM/Tool/최종·종료 Trace, 공급자 usage와 비밀 보호 | 메모리 Trace와 종료 JSONB 저장, allowlist metadata, raw invalid 인자 및 reasoning 비저장 검사 | 검증한 경로 충족 |
| 고정 데이터·UTC·정책과 실제 통신 없는 E2E, 품질과 구분 | scripted fake, 독립 DB E2E, 고객 snapshot 보존, SDK MockTransport 및 기존 Backend 회귀 | 충족 |

### policy-search

| MUST 요구사항 | 구현·검증 근거 | 판정 |
|---|---|---|
| 플랜·역할·구독/결제·초대 정책과 고정 ID/title/keywords/content | 세 JSON 문서가 member_limit 및 D7의 규칙을 따름. 임의의 플랜별 한도·환불 자격 없음 | 충족 |
| query 검증, 최대 3개, 결정적 점수/ID 순서, 빈 결과 | PolicyQuery 및 casefold/공백 token 검색, 반복·동점·token 점수·잘못된 입력 테스트 | 충족 |
| 읽기 전용, 당시 반환 내용 보존, 미관찰 ID 거부와 로딩 실패 종료 | 실행당 한 번 load, 관찰 전체 JSONB 저장, 정책 변경 후 Trace 보존 및 invalid_output/tool_error 검사 | 충족 |

### tasks.md 완료 표시의 의미

23개 완료 표시는 구현·자동 검증 작업 대부분과 대응한다. 그러나 [5.3](../openspec/changes/support-agent/tasks.md#L32)의 “전체 deadline 구현”은 SA-01 때문에 완전 충족으로 받아들일 수 없다. 실행 중 조회 검증 역시 안정적인 running 상태만 검사하며 SA-02 경합을 잡지 못한다. 완료 체크나 strict validation만으로 모든 시나리오가 검증됐다고 주장해서는 안 된다.

[7.3](../openspec/changes/support-agent/tasks.md#L47)은 설정 부재 시 미실행을 명시하도록 되어 있다. 바로 아래 기록에도 실제 Smoke와 계정·모델 확인을 하지 않았다고 적혀 있으므로, 그 체크 자체를 실제 호출 성공의 허위 보고로 분류하지 않는다. 향후 환경 설정을 제공하면 여전히 실제 Smoke가 필요하다.

## 4. Findings (P0~P3)

P0: 없음. P1: 없음. P2: 아래 두 건. P3: 없음.

### SA-01 / P2 — 동기 SDK 호출 내부에서는 전체 실행 deadline을 강제하지 못함

**Location:** [app/agent/loop.py:37](../app/agent/loop.py#L37), 같은 파일 48~55, 68~84행. 관련 DB 대기 설정은 [app/db.py:13](../app/db.py#L13).

**Problem:** `remaining()`은 SDK 호출 전과 반환 후에만 실행된다. SDK에 전달한 float timeout은 HTTPX의 connect/read/write/pool 대기 timeout이며 요청 전체의 절대 종료 시간과 다르다. read timeout보다 짧은 간격으로 본문이 계속 수신되면 `responses.create()`는 Agent deadline이 지난 뒤에도 응답 본문을 기다린다. 따라서 deadline은 후속 행동 차단에는 작동하지만 진행 중인 모델 호출의 전체 시간 상한을 강제하지 못한다. HTTPX는 read timeout을 각 데이터 chunk 수신 대기 시간으로 정의한다. [HTTPX 공식 timeout 문서](https://www.python-httpx.org/advanced/timeouts/).

**Impact:** 느리게 진행되는 응답 수신에서 문의가 설정 시간 이상 running으로 남고 HTTP 요청 worker를 계속 점유한다. 앞선 Tool이 DB 읽기 transaction을 열었다면 해당 연결도 더 오래 보유한다. 응답이 돌아온 뒤에는 execution_timeout으로 실패하고 추가 행동을 중단하므로, 잘못된 정상 진단을 저장하는 문제는 확인하지 않았다. 일정한 수신 진행이 유지되는 동안에는 현재 검사만으로 설정 deadline 시점의 종료를 보장할 수 없다는 것이 영향 범위다. 실제 OpenAI에서 이 조건이 발생했다고 측정한 것은 아니다.

**Evidence:** 소스·테스트 수정 없이 `run_agent`→기존 `responses_create`→OpenAI SDK 2.54.0→실제 HTTPX transport 경로를 localhost로 연결했다. 가짜 서버는 정상 Responses JSON 본문을 10개 조각으로 약 0.08초 간격으로 보내고 종료했다. Agent budget은 0.2초였다. 첫 호출은 약 1.387초, SDK 초기화 영향을 줄인 두 번째 호출도 약 **0.761초** 후에야 종료되었다.

```text
attempt=2, budget_seconds=0.2, elapsed_seconds=0.761
error_code=execution_timeout, llm_duration_ms=759
```

기존 `test_timeout_after_response_stops_tools`, `test_remaining_timeout`, `test_llm_timeout_after_budget`은 fake가 반환하거나 예외를 던진 뒤의 검사를 검증한다. 진행 중인 실제 I/O를 중단하는지는 검사하지 않는다.

또한 DB pool/connect timeout은 고정 5초이고 Tool의 statement/lock timeout은 연결을 확보한 뒤 설정한다. 이 설정만으로 남은 budget이 5초보다 작을 때 연결 획득 대기까지 budget에 맞춰진다고 설명할 수 없다. 이 DB 조건은 별도로 강제 재현하지 않았으므로 추가 Finding이나 실제 DB 지연 측정으로 주장하지 않는다.

**Recommended Fix:** 현재 `responses_create`라는 작은 호출 경계에서 요청 전체의 절대 deadline을 적용하고 만료 시 진행 중인 I/O를 취소·종료하도록 보완한다. SDK의 phase timeout과 `max_retries=0`은 유지한다. 동기 Loop·Application·기존 Tool의 책임을 바꿀 필요는 없다. 단순히 thread/future의 결과 대기만 timeout으로 반환하고 기존 I/O를 계속 실행시키는 방식은 실행 종료를 증명하지 못한다. 보완 검증에는 실제 로컬 transport의 지연 응답·분할 수신과 이후 호출 중단을 포함하고, 연결 획득 대기의 budget 반영도 확인한다.

이 Finding은 명세가 허용한 “전체 HTTP 응답 시간이 정확히 60초 이내일 필요는 없음”과 구분한다. 인증·claim·종료 저장의 별도 시간은 수용된 설계다. 여기서 확인한 초과는 **Agent 내부의 모델 호출 자체**에서 발생했다.

### SA-02 / P2 — 동시 종료 commit 중 GET 문의 표현이 서로 다른 시점의 상태와 진단을 결합함

**Location:** [app/services/support.py:25](../app/services/support.py#L25), 같은 파일 31~33행. 진입 경로는 [app/api/support.py:19](../app/api/support.py#L19).

**Problem:** GET은 `get_request()`의 SELECT로 상태·실행 시각을 읽고, `request_dto()`가 별도의 SELECT로 Diagnosis를 읽는다. PostgreSQL READ COMMITTED에서 두 SELECT 사이에 종료 commit이 발생하면 첫 조회의 running 상태와 두 번째 조회의 완료 Diagnosis가 결합된다. RequestDTO는 상태별 diagnosis 일관성을 별도로 검사하지 않는다.

**Impact:** 진단 종료와 경합하는 polling GET이 `status=running`, `finished_at=null`이면서 진단을 가진 응답을 반환할 수 있다. `support-requests`의 실행 중 조회 계약인 running/null diagnosis와 어긋나며 클라이언트가 상태와 결과를 모순되게 해석할 수 있다. 실제 저장은 completed/Diagnosis/Trace를 원자적으로 반영하므로 저장 손상은 아니다.

**Evidence:** 임시 PostgreSQL schema에서 실제 API로 문의를 생성하고 다른 Session의 diagnose를 진행했다. GET의 첫 문의 SELECT가 running을 읽은 직후, 원래 `request_dto()`의 Diagnosis SELECT 전에 diagnose의 정상 종료 commit을 완료시키도록 일시적으로 호출 순서를 제어했다. 생산 함수의 두 SELECT와 실제 DB 값은 그대로 사용했다.

```text
GET_RACE: status='running', has_diagnosis=True, finished_at=None
DURABLE_AFTER_RACE: status='completed', has_diagnosis=True
```

기존 `test_concurrent_claim_and_running_reads`는 LLM fake를 정지시킨 상태에서 GET을 끝낸 뒤 실행을 재개한다. 따라서 두 SELECT 사이의 종료 commit을 다루지 않는다.

**Recommended Fix:** 현재 일회 실행·terminal 결과 불변 계약을 이용해 `request_dto()`가 요청 상태가 completed일 때만 Diagnosis를 조회하도록 하고, pending/running/failed에는 null을 반환한다. 이 방식이면 첫 SELECT의 상태 시점에 맞는 표현을 유지할 수 있다. 상태·Diagnosis를 한 SQL 문장으로 읽는 방법도 가능하지만 MVP에서는 반드시 필요한 구조 변경은 아니다. 두 SELECT 사이 종료 commit을 재현하는 회귀 테스트로 응답의 상태·diagnosis·finished_at 일관성을 확인한다.

## 5. Agent / Application Architecture Assessment

### 실제 호출 경로와 책임

```text
지원 Router
  → 공통 Bearer/User 인증 + 요청 Session
  → Support Application: 조직 범위, pending claim, 시작 commit
  → Single Agent Loop: Responses, 호출 수/deadline 검사, 메모리 Trace
  → Tool 어댑터: 고정 이름·Pydantic 인자·안전한 관찰
  → 기존 customer 조회 Service / 로컬 policy 검색
  → Support Application: 결과·관찰 참조 검증, 종료 원자 저장
```

Application이 생성자·조직을 HTTP 인증 값으로 결정한다. Tool 인자 schema에는 user_id 또는 organization_id 또는 query만 있으며 CallerContext, Session, role을 받지 않는다. UUID 유효성 검사가 접근 권한 검사를 대체하지 않고, 실제 고객 Service가 다시 조직 범위를 강제한다. 다른 조직과 없는 대상은 같은 not_found다. 조회 DTO를 JSON 관찰로 만들며 HTTP 응답 객체를 Tool 결과로 반환하지 않는다.

Loop는 고객 조회 순서를 고정하지 않는다. function_call을 응답 순서로 처리하고, call_id의 누락·중복을 실행 전에 검사하며, 잘못된 Tool 시도도 16회 한도에 포함한다. response.output을 누적하고 각 function_call_output을 JSON으로 추가한다. reasoning 항목은 다음 입력에 남지만 DB Trace에는 포함되지 않는다. 함수 호출이 있는 턴의 텍스트를 최종 진단으로 확정하지 않는다.

ORM 쓰기와 commit/rollback은 Support Application에 있다. 인증의 SELECT가 autobegin을 시작한 뒤에도 무조건 `session.begin()`을 호출하지 않는다. 시작 claim은 조직과 pending을 조건으로 하는 UPDATE이고 commit 후에만 모델을 실행한다. 종료 저장은 상태 UPDATE, Diagnosis, 전체 AgentTrace, DTO 구성 후 한 commit으로 이루어진다. SQLAlchemy 오류는 rollback 후 실패 저장을 한 번 시도하며 원래 오류를 유지한다. 이미 terminal인 결과를 덮어쓰는 경로는 확인하지 못했다.

Application이 최종 참조 유효성을 검증하는 경계도 유지된다. 다만 `run_agent`는 JSON 문자열과 전달받은 Trace list로 결과를 전달하고 Support Service는 `AgentFailure`에 의존한다. 현재 한 Loop에서는 명확히 추적 가능하며, 이 결합만을 이유로 Provider interface나 결과 framework를 추가할 필요는 없다.

### 다음 Workflow Baseline 및 Evaluation에 대한 판단

기존 customer 조회 함수와 DTO, CallerContext, policy 검색은 HTTP와 무관하게 재사용 가능하다. Workflow는 같은 Service와 동일한 문서 집합·기준 UTC를 사용해 조건을 맞출 수 있다. 지금 별도의 Repository, Unit of Work 또는 Agent framework를 도입할 실질적 필요는 없다.

Trace에는 선택된 Tool, 검증된 인자, 실제 관찰, 오류, 공급자 token usage, model/response_id, 소요시간이 있다. Diagnosis에는 outcome와 원인·행동·근거가 있어 평가 입력으로 사용할 수 있다. 실제 가격·비용 계산, 정답 판정과 Workflow/Agent 비교 실행기는 현재 Change 범위 밖이다. SA-01을 고치기 전에는 timeout 설정을 측정 실행의 엄격한 latency 상한으로 간주하면 안 된다.

고객 데이터 쓰기를 하는 Tool이나 불필요한 비동기 작업 인프라, Multi-Agent, MCP는 추가되지 않았다. 두 Finding 모두 현재 책임 경계를 유지하는 방향으로 보완할 수 있다.

## 6. Test Coverage & Gaps

### 실질적으로 검증하는 부분

- scripted Responses fake는 실제 Service 결과가 다음 입력의 function_call_output에 포함되는지 검사한다. 구독 이후 결제·정책 조회, 여러 호출의 순서, call_id 및 opaque reasoning 전달을 검증한다.
- E2E는 HTTP 생성→동기 진단→독립 요청의 결과/Trace 조회를 실행한다. 역할·팀원 한도·만료·past_due·실패 결제·부재·정보 요청·이관과 고객 네 테이블 snapshot 보존을 확인한다. 최종 판단 자체는 script가 정하므로 실제 모델의 원인 진단 능력 검증은 아니다.
- strict Tool schema와 추가 인자 거부를 별도로 검사한다. customer Service를 spy로 감싼 테스트도 원래 조회 함수를 호출한다. 고객·지원 HTTP 및 Tool 직접 호출의 조직 경계를 실질적으로 검증한다.
- durable_db/durable_app은 외부 savepoint를 사용하지 않고 요청마다 Session을 열어 commit한다. 독립 Session 재조회는 성공 저장의 지속성을 확인하고, 별도 요청의 concurrent claim은 한 실행만 모델을 호출하는 것을 확인한다.
- DB 오류 주입은 실제 PostgreSQL에서 진행된 transaction을 rollback하도록 한다. read/flush/commit 오류, 실패 저장까지의 지속 오류, 정상 commit 후 오류를 던지는 불명확 결과를 검증한다. 이를 단순 fake 저장 성공으로 대체하지 않는다.
- MockTransport 테스트는 설치한 실제 SDK의 요청 생성과 응답 역직렬화를 거친다. 별도의 SDK 생성 fake는 max_retries=0을 확인한다. 후자는 실제 HTTP 장애 재시도 횟수를 관측한 테스트는 아니다.

### 확인된 공백과 검증 한계

| 공백 | 판단 |
|---|---|
| 진행 중인 I/O의 전체 deadline, phase별 누적 대기, pool/connect 잔여 budget | fake clock 검증만으로는 충분하지 않음. SA-01은 로컬 실제 transport로 재현했다. |
| GET의 문의 SELECT와 Diagnosis SELECT 사이 종료 commit | 기존 concurrent 테스트의 제어 순서로는 놓침. SA-02는 별도 PostgreSQL API 재현으로 확인했다. |
| Trace INSERT/flush가 실제 DB 제약 오류를 내는 종료 저장 실패 | 현재 flush 주입은 Diagnosis가 session.new에 있을 때 INSERT 전에 실패한다. 전체 transaction 경계는 올바르게 보이나 Trace 저장 중 실패를 직접 재현한 회귀 케이스는 없다. 코드에서 별도 결함을 확인하지 않아 추가 Finding으로 만들지 않았다. |
| 실제 연결 끊김·commit acknowledgement 유실 | 현재 ambiguous 케이스는 commit 성공 후 예외를 발생시켜 상태 보호 분기를 검증한다. 실제 네트워크 단절·재연결까지 증명한 것은 아니다. |
| 공급자 응답의 엄격한 형식과 선택 모델 지원 | fake는 id/role 등 일부 실제 응답 필드를 생략하며 SDK의 permissive parsing을 통과한다. SDK serialization 검증과 실제 서버의 schema 수락은 별개다. |
| 실제 모델의 Tool 선택·진단 품질·정책 준수 | Key/모델 부재로 미검증. 조건부 Smoke와 후속 Evaluation에서 확인할 사항이며 테스트 수로 대체할 수 없다. |

테스트의 시간·usage·event 필드 검사 깊이에는 차이가 있다. 핵심 오류 코드와 token allowlist·reasoning 비저장은 검사하지만 모든 호출의 정확한 소요시간이나 모든 형태의 실제 공급자 오류를 증명하지 않는다. 실제 문제를 재현한 두 항목과 일반적인 추가 검증 가능 항목을 구분했다.

## 7. Known Trade-offs (현재 수용 가능한 사항)

다음은 명세에서 의도적으로 수용한 사항이며 새 결함으로 분류하지 않았다.

- **동기 실행과 DB 점유:** 첫 Tool의 timeout 설정/조회가 transaction을 연 뒤 다음 LLM 호출을 기다리는 동안 Session·connection이 유지될 수 있다. 고객 조회 순서 고정이나 Tool별 Session 분리는 하지 않는다. pool/connect/statement/lock에 유한 timeout은 있다. SA-01에 따른 budget 초과는 이 설계 비용과 별도의 문제다.
- **종료 일괄 Trace 저장:** running 조회의 Trace는 빈 배열일 수 있다. 프로세스 종료나 지속 DB 장애는 running 잔존과 메모리 Trace 유실을 일으킬 수 있다. 자동 복구·resume·retry API는 범위 밖이다.
- **같은 조직의 문의 공유:** owner/admin/member 모두 같은 지원 데이터에 접근한다. 현재 역할별 제한을 임의로 추가할 근거는 없다.
- **개발용 Bearer 인증:** 토큰 로그인·회전·만료는 제공하지 않는다. 실제 운영 인증으로의 일반화는 이 Change 요구가 아니다.
- **업무 결과와 HTTP 계약:** needs_information/escalate는 completed다. 처리 가능한 기술 실패도 저장 성공 시 HTTP 200/failed이고 DB 실패는 503이다. 이 구분은 의도된 계약이다.
- **최소 DB 제약:** completed에 Diagnosis가 있어야 하는 상태 간 불변식은 Application이 원자 저장한다. 배열 구조 전체를 DB CHECK나 trigger로 중복 구현하지 않는다.
- **고객 조회 READ COMMITTED:** 여러 Tool의 고객 관찰 전체를 같은 DB snapshot으로 묶지 않는다. 각 관찰을 Trace에 보존한다. SA-02는 이 고객 snapshot trade-off가 아니라 하나의 지원 DTO 내부의 모순이다.
- **단순 정책 검색:** 형태소·의미 검색은 하지 않는다. 고정 키워드와 결정적 정렬을 사용하고, 없는 결과는 빈 items로 전달한다.
- **근거의 의미 검증 한계:** 성공 관찰과 유효한 정책·Trace 참조를 검사하지만 statement와 cause가 관찰을 논리적으로 정확하게 해석했는지까지 강제하지 않는다.
- **총 HTTP 시간:** 인증·claim·종료 저장은 Agent budget 외의 별도 DB 대기를 포함할 수 있다. 정확히 60초 이내의 HTTP 응답은 요구하지 않는다.

## 8. Recommended Actions

1. **SA-02 보완:** status가 completed일 때만 diagnosis를 구성하도록 최소 수정하고, SELECT 사이 정상 종료 commit의 회귀 케이스를 추가한다. 지속 상태의 원자 저장 구조는 유지한다.
2. **SA-01 보완:** SDK 호출 경계에서 전체 요청 deadline을 실제로 종료시킬 수 있게 하고, 지연·분할 수신으로 검증한다. DB 연결 획득과 복수 statement의 잔여 budget 반영도 함께 확인한다. Evaluation의 timeout·latency 결과를 해석하기 전에 완료한다.
3. **Workflow Baseline:** 같은 customer Service/DTO, policy 문서, 인증 조직 범위 및 기준 UTC를 재사용한다. 지금 새로운 저장·권한 추상화를 만들 이유는 없다.
4. **실제 환경 확인:** 명시적인 Key·모델과 비용 발생 실행 조건이 준비되면 복합 문의 Smoke를 실행하여 실제 후속 Tool 선택·저장 근거·종료를 확인한다. fake 통합 검증과 별도 기록으로 남긴다.
5. **선별적 실패 검증:** 다음 검증 보완에서는 Trace 저장 도중 실제 오류 및 연결 무효화 후 실패 저장·terminal 보호를 우선한다. 테스트 수를 늘리는 것 자체를 목표로 삼지 않는다.

이 리뷰에서는 위 수정이나 테스트 추가를 수행하지 않았다. 기존 코드·명세·테스트 파일을 그대로 보존했다.

## 9. Final Verdict

**READY WITH MINOR FIXES.**

핵심 API·Agent 관찰 전달·조직 경계·Application 소유 저장 구조는 다음 Workflow Baseline 구현에 사용할 수 있다. 확인된 P2 두 건은 지원 응답의 동시성 일관성과 실행 시간 제어에 국한되며, P0/P1 또는 데이터 손상·조직 경계 실패는 확인하지 못했다. 전체 구조 재설계는 필요하지 않다.

SA-02를 보완하고, SA-01을 실제 deadline/latency에 의존하는 평가 전에 수정한다. 전체 pytest와 OpenSpec 통과만으로 미검증 경계나 실제 모델 품질이 확보되었다고 표현해서는 안 된다. 현재 검증은 Application과 Agent 실행·저장 경로에 대한 것이며 실제 OpenAI Smoke와 진단 품질 평가는 남아 있다.
