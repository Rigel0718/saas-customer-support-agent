# support-agent — P2 Finding 수정 기록

수정일: 2026-10-15. 대상은 독립 리뷰의 SA-01과 SA-02다. [독립 리뷰 원본](support-agent-review.md)은 변경하지 않았다.

## 1. 결과와 다음 단계

| Finding | 최종 분류 | 근거 |
|---|---|---|
| SA-02 — GET 상태/Diagnosis 경합 | **RESOLVED** | request_dto가 completed 상태에서만 Diagnosis를 조회한다. 다른 Session의 종료 commit을 첫 조회와 DTO 구성 사이에 끼운 실제 PostgreSQL/API 회귀 테스트가 통과했다. |
| SA-01 — 전체 Agent deadline | **PARTIALLY RESOLVED** | 진행 중인 모델 HTTP I/O는 실제 취소·연결 종료를 검증했다. 다만 동기 SQLAlchemy pool/connect 대기는 같은 취소 범위 밖이며, pool 획득이 남은 Agent budget을 초과하는 조건을 실제로 재현했다. 전체 Agent의 엄격한 wall-clock 상한은 아직 보장하지 않는다. |

기존 동기 Agent/Application/Tool 구조를 유지하며 Workflow Baseline 구현은 진행할 수 있다. Evaluation은 실제 경과 시간을 측정하고 DB 오류·실행 timeout·모델 품질 결과를 분리해야 한다. `AGENT_TIMEOUT_SECONDS=60`을 전체 실행이나 HTTP 응답의 엄격한 상한으로 사용하거나, 60초를 초과한 측정치를 60초로 잘라 보고하면 안 된다.

전체 pytest는 **185 passed, 1 warning in 11.10s**이며 `openspec validate support-agent --strict`는 통과했다. 실제 OpenAI API는 호출하지 않았다. 기능 전체의 모든 deadline 조건을 해결했다고 주장하지 않으며, 이 기록은 독립 리뷰 원본의 판정을 소급해서 변경하지 않는다.

## 2. SA-02: 원인과 수정

### 원인

`get_request()`는 상태·시각을 읽고 `request_dto()`는 별도 SELECT로 Diagnosis를 읽었다. READ COMMITTED에서 두 조회 사이에 종료 transaction이 commit되면, 첫 조회의 running/finished_at=null과 두 번째 조회의 Diagnosis가 결합될 수 있었다. 저장 원자성이 깨진 것은 아니며 응답 구성의 경합이었다.

### 실제 수정

[app/services/support.py](../app/services/support.py#L31)의 request_dto에서 조회한 SupportRequest의 status가 **completed일 때만** Diagnosis를 읽도록 변경했다. pending/running/failed는 diagnosis=null이다. 현재 일회 실행과 terminal 결과 불변 계약에 따라 첫 상태 조회의 시점에 맞는 DTO를 유지한다.

상태 전이, 조건부 UPDATE, commit/rollback, 모델, 조직 WHERE 및 기존 고객 Service/DTO는 변경하지 않았다. completed 조회와 종료 저장 직후의 DTO는 기존 방식으로 저장된 Diagnosis를 반환한다.

### 회귀 검증

`test_get_keeps_running_snapshot_during_terminal_commit`은 다음 순서를 실제 PostgreSQL의 별도 요청 Session으로 실행한다.

1. 문의 생성 후 diagnose의 모델 응답을 잠시 정지해 running을 확정한다.
2. GET의 첫 SELECT가 running을 읽은 후, 원래 request_dto 실행 전에 다른 Session의 diagnose를 완료·commit한다.
3. GET은 첫 조회 시점의 running, diagnosis=null, finished_at=null을 반환한다.
4. 이후 GET은 completed, 저장된 Diagnosis 및 finished_at을 반환한다.

이 테스트는 호출 순서만 제어하고 DB 조회 결과나 commit을 가짜 값으로 교체하지 않는다. 수정으로 non-completed의 Diagnosis SELECT가 없어져, 원래 두 SELECT 사이에 해당하던 경합 창에서 진단이 새로 읽히지 않는다.

## 3. SA-01: SDK 확인과 실제 수정

### 원인과 확인한 동작

설치 버전은 OpenAI **2.54.0**, HTTPX **0.28.1**, HTTPcore **1.0.9**다. 기존 float timeout은 요청 전체 시간이 아니라 connect/read/write/pool의 대기 제한이다. 특히 read timeout보다 짧은 간격으로 조각이 계속 도착하면 동기 responses.create는 전체 Agent deadline 후에도 기다릴 수 있었다. [HTTPX 공식 timeout 문서](https://www.python-httpx.org/advanced/timeouts/).

SDK의 AsyncOpenAI와 async client 정리 경로, HTTPcore의 AnyIO read/write/close 구현을 확인했다. Python 3.11의 asyncio.timeout은 기다리는 Task를 취소하고 해당 취소가 끝난 뒤 TimeoutError로 변환한다. [Python 공식 asyncio 문서](https://docs.python.org/3.11/library/asyncio-task.html#asyncio.timeout). 공식 SDK도 async Responses 호출과 async context manager 사용을 지원한다. [OpenAI Python SDK 문서](https://developers.openai.com/api/reference/python).

이 문서만으로 실제 연결 종료를 입증한 것으로 취급하지 않고, 설치된 SDK와 실제 localhost TCP transport로 별도 검증했다. 문서의 최신 HTTP client 기본값과 설치 버전은 다를 수 있으며, 이번 검증 경로는 HTTPX/HTTPcore다.

### 선택한 최소 방법

[app/agent/loop.py](../app/agent/loop.py#L35)의 기존 동기 함수 responses_create를 유지하고, 그 함수 **내부의 SDK HTTP 호출만** AsyncOpenAI로 변경했다.

```text
동기 Support Application / Agent Loop
  → responses_create(settings, budget_seconds=남은 시간, ...)
  → asyncio.run(단일 SDK 요청 coroutine)
  → asyncio.timeout(남은 budget)
  → async with AsyncOpenAI(max_retries=0, timeout=phase timeout)
  → await client.responses.create(...)
```

남은 budget으로 monotonic deadline을 잡고, coroutine 시작까지 걸린 시간도 차감하여 timeout 범위를 만든다. deadline 만료 시 진행 중인 await를 취소하고 async client context를 빠져나와 연결을 닫는다. 별도 thread/future의 결과 대기만 포기하는 방식이 아니다. 함수가 반환하거나 실패할 때 요청 coroutine과 이벤트 루프의 정리가 끝난다.

Agent 전체를 async로 변경하지 않았다. Router, Support Service, Tool 및 SQLAlchemy Session은 기존 동기 호출 경계를 유지한다. Worker, 프로세스 실행기, Celery, Redis, 범용 timeout framework 또는 새로운 의존성은 추가하지 않았다. Python 3.11은 기존 프로젝트의 최소 버전이다.

기존 phase timeout=min(20초, 남은 시간), max_retries=0 및 호출 수 제한을 유지한다. overall timeout 범위의 만료는 안전한 AgentFailure(execution_timeout)으로 전달한다. Loop는 이 명시적 오류도 execution_timeout으로 분류하며, 전체 deadline 전에 발생한 SDK phase timeout이나 HTTP 오류는 llm_error로 처리한다. 어느 경우든 추가 모델/Tool 호출을 실행하지 않는다. Application이 기존 방식으로 실패 상태와 Trace를 저장한다.

## 4. 실제 I/O 회귀 테스트

새 [tests/test_agent_deadline.py](../tests/test_agent_deadline.py)는 Fake Clock이나 MockTransport만으로 실제 취소를 주장하지 않는다. localhost의 ThreadingHTTPServer, 실제 AsyncOpenAI, HTTPX AsyncHTTPTransport 및 TCP socket을 사용한다. 서버·스레드는 테스트용이며 Application에는 추가하지 않았다.

| 테스트 | 확인한 내용 |
|---|---|
| `test_deadline_cancels_real_http_io[headers]` | 서버가 헤더를 보내지 않는 대기 중 Agent deadline으로 취소한다. 서버가 TCP EOF/연결 종료를 관측한다. |
| `test_deadline_cancels_real_http_io[trickle]` | 정상 Responses JSON을 8-byte 조각으로 0.04초 간격 전송한다. 계속 수신되더라도 deadline으로 종료하고 서버가 전체 본문을 끝까지 보내지 않는다. |
| `test_deadline_cancels_real_http_io[upload]` | SDK가 만든 JSON을 테스트의 AsyncByteStream으로 천천히 분할해 실제 TCP에 업로드한다. 서버는 Content-Length보다 적은 본문과 EOF를 받아 업로드 취소를 확인한다. HTTP 응답을 가짜 결과로 대체하지 않는다. |
| `test_phase_timeout_before_deadline_is_llm_error` | 전체 budget 전에 발생한 실제 SDK read timeout은 llm_error다. 연결은 종료되며 HTTP 요청은 한 번이다. |
| `test_sdk_http_error_has_no_retry` | 실제 로컬 503 응답에도 요청 1회, retry-count=0, llm_error다. |
| `test_real_sdk_normal_tool_loop` | 실제 SDK/HTTP 요청 두 번과 PostgreSQL 구독 조회를 연결한다. 다음 입력의 call_id별 관찰과 최종 근거 검증이 유지된다. |
| `test_http_cancel_saves_safe_failure_without_more_calls` | 지원 API에서 timeout→200/failed/execution_timeout→독립 GET 결과/Trace를 검증한다. Tool 호출 없음, HTTP 모델 요청 1회, 재진단 409, Key 비저장을 확인한다. |
| `test_database_pool_wait_can_exceed_remaining_budget` | 실제 PostgreSQL 연결로 아래 남은 DB 획득 한계를 기록한다. |

네트워크 취소 테스트의 Agent budget은 1초이고 종료 시간 검사에는 스케줄링·SDK 정리 여유 0.7초를 둔다. 이 시간 검사만으로 정확히 1.000초 종료를 보장한다고 주장하지 않는다. **서버의 EOF/연결 종료, 불완전 업로드, 닫힌 HTTPX client, 추가 호출 부재**를 함께 검사하는 것이 실제 취소의 근거다.

업로드 테스트의 조각·속도 제어는 실제 HTTPX transport 앞의 테스트 전용 stream이다. Production SDK의 JSON 직렬화를 그대로 거쳐 실 TCP로 보내며, timeout 후 연결은 닫힌다. 실제 OpenAI 서버나 유료 모델은 호출하지 않았다.

## 5. Deadline 보장 범위와 남은 한계

### 검증한 범위

- 현재 동기 호출 경계에서 수행하는 **모델 HTTP 업로드·헤더 수신 대기·분할 응답 수신**은 남은 budget에 따른 취소와 연결 종료를 실제 로컬 transport에서 확인했다.
- 정상 Responses 후속 Tool Loop와 DTO/관찰 직렬화는 유지된다.
- SDK 재시도는 0이며 timeout 이후 추가 LLM/Tool 호출과 정상 diagnosis 저장이 없다.
- 취소 이후 Application의 종료 상태·Trace 저장은 기존 transaction 경계를 사용한다.

### 동기 DB 획득의 한계

`build_engine()`의 QueuePool timeout과 psycopg connect_timeout은 고정 5초다. Tool은 Session이 연결을 확보한 뒤 statement/lock timeout을 남은 시간에 맞추지만, **연결을 얻기까지의 대기는 asyncio 취소 범위 밖**이다. 기존 고객 Service의 여러 SQL 문장도 Tool 시작 시 설정한 문장별 timeout을 사용하므로, 전체 Tool의 소요시간 상한과 동일하지 않다.

실제 연결을 한 개 가진 QueuePool을 모두 점유하고, 테스트용 pool_timeout=0.3초 / Agent budget=0.05초로 같은 획득 경로를 실행했다. QueuePool이 약 0.3초를 기다린 뒤 SQLAlchemy TimeoutError를 반환하는 것을 검증했다. Agent budget보다 길며, Tool 본체와 다음 LLM은 실행되지 않고 database_error Trace가 남는다. Production 엔진의 pool timeout=5초도 별도로 확인했다. 기존 계약상 DB 오류는 Application의 rollback/실패 저장 시도와 HTTP 503 처리 대상이다.

설치된 psycopg의 connect_timeout 해석에도 최소 2초 규칙이 있다. 값을 무조건 남은 sub-second budget으로 바꾸는 것만으로 연결 timeout을 정확히 강제할 수 없다. 이 부분은 소스로 확인했으며, 실제 DNS/접속 장애를 추가로 측정한 것은 아니다.

현재 public 동기 Session/QueuePool 경계에서 request별 대기를 취소하는 검증된 최소 수정을 확보하지 못했다. pool의 공유 timeout을 요청마다 변경하면 다른 요청에 영향을 주고, 별도 thread에서 Session을 취소하거나 DB 작업을 남겨두는 방법은 안전성을 입증하지 못한다. 이를 무리하게 추가하지 않고 **SA-01을 PARTIALLY RESOLVED로 유지**한다.

### 그 밖의 보장 한계

- asyncio 취소는 await 가능한 I/O 경계에서 작동한다. SDK 구성·JSON 직렬화/파싱·정책 파일 읽기·결과 검증 같은 동기 CPU/파일 작업을 강제로 선점하지 않는다.
- 현재 경로는 FastAPI의 동기 worker 또는 동기 CLI에서 사용한다. 이미 실행 중인 이벤트 루프의 coroutine에서 responses_create를 직접 호출하는 사용법은 지원 범위가 아니다.
- localhost HTTP/TCP 취소를 검증했으며 DNS 해석, HTTPS/TLS, proxy 및 사용자 정의 transport의 모든 취소 동작을 검증한 것은 아니다. asyncio.run의 이벤트 루프/기본 executor 정리는 소요시간을 포함할 수 있다.
- 클라이언트 연결 종료는 공급자 서버의 모델 계산이나 청구 중단을 보장하지 않는다. 이번에는 실제 공급자를 호출하지 않았다.
- 인증·claim·종료 transaction은 기존 설계대로 Agent budget 밖이다. HTTP 총 응답 시간도 60초 상한으로 보장하지 않는다.

### 가능한 후속 선택과 Trade-off

현재 범위에서는 검증된 모델 I/O 취소를 적용하고 동기 DB 대기를 명시적으로 한계로 남기는 선택이 가장 작다. DB까지 엄격히 제한하려면 요청별 연결 획득과 driver 취소를 지원하는 경계, 또는 시작 전에 연결을 확보하고 실행 동안 보유하는 수명 변경을 별도로 검증해야 한다. 전자는 현재 동기 DB 구조의 수정 비용과 취소 안전성 검증이 필요하며, 후자는 초기 대기를 별도로 다루고 더 오래 연결을 점유하는 비용이 있다. 이번 작업에서는 고객 Service 계약이나 Session 소유권을 바꾸지 않았다.

전체 구조를 async로 재작성하거나 Worker를 추가하는 방법은 이번 제한을 벗어나므로 도입하지 않았다. 따라서 “전체 Agent 60초 강제 해결”을 완료로 표현하지 않는다.

## 6. 변경 파일과 코드 경계

| 파일 | 이번 변경 |
|---|---|
| `app/services/support.py` | request_dto의 completed 조건부 Diagnosis 조회만 변경 |
| `app/agent/loop.py` | 동기 SDK 호출 함수 안의 취소 가능한 async HTTP I/O, 남은 budget 전달, 명시적 execution_timeout 분류 |
| `tests/test_support_execution.py` | 독립 Session 종료 commit과 GET의 경합 회귀 1건 추가 |
| `tests/test_agent_deadline.py` | 실제 로컬 TCP·SDK 취소, 정상 경로, 안전한 저장 및 DB 획득 한계 회귀 8개 추가 |
| `tests/test_agent_loop.py` | 기존 SDK 생성 검사 fake를 AsyncOpenAI context/create에 맞춤 |
| `study/support-agent-fixes.md` | 이 수정 기록 |

이 작업 전부터 support-agent 구현이 미추적/미커밋 상태였다. `git diff`와 실제 파일을 확인하고 작업 전 파일별 hash와 대조해 이번 변경 범위를 구분했다. 리뷰 원본·기존 고객 Service/DTO·데이터 모델·정책·OpenSpec 기능 명세·설정·의존성은 이번 작업에서 변경하지 않았다.

## 7. 실행한 검증

- 초기 개별 검증: 네트워크 취소/정상 Loop/저장, SA-02 경합 및 기존 SDK 생성 검사 **9 passed**.
- DB 획득 한계 추가 검증: 해당 실제 QueuePool 재현 **1 passed**.
- `uv run pytest -q`: **185 passed, 1 warning in 11.10s**. 기존 고객 API·조직 격리·동시 claim·종료 원자성·불명확 commit 결과 보호를 포함한다.
- `openspec validate support-agent --strict`: **통과**.
- `git diff --check`: **통과**.
- 리뷰 원본 hash 대조: **동일**.
- 실제 OpenAI API 호출: **미실행**. 이 작업에서는 로컬 서버와 가짜 Key/모델만 사용했다.

warning은 기존 Starlette/HTTPX deprecation 경고다. 이번 변경과 무관한 의존성 교체는 하지 않았다. 자동 검증은 기존 Application과 SDK 연결·취소에 대한 것이며 실제 Agent 품질 또는 Workflow 대비 성능 개선을 입증하지 않는다.
