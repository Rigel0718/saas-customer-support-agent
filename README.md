# SaaS Customer Support Backend

Python 3.11+와 uv, Docker Compose가 필요하다. 현재 범위는 고객 읽기 API, 개발용 인증, Support Agent와 Workflow Baseline이다. Evaluation Framework와 Trade-off Analysis는 후속 Change에서 구현한다.

## 로컬 실행

`.env.example`을 참고해 환경을 설정한다. 실제 토큰과 비밀번호는 로컬에서 정하고 Git에 저장하지 않는다. 아래 환경 변수는 예시 자리표시자이므로 교체한다.

```bash
export POSTGRES_PASSWORD='replace-with-local-password'
export DATABASE_URL="postgresql+psycopg://support:${POSTGRES_PASSWORD}@localhost:55432/support_dev"
export TEST_DATABASE_URL="postgresql+psycopg://support:${POSTGRES_PASSWORD}@localhost:55432/support_test"
export DEV_BEARER_TOKENS='{"replace-with-local-token":"00000000-0000-0000-0000-000000000101"}'
uv sync --locked
docker compose up -d --wait
uv run python -m app.db
uv run python -m app.seed
uv run python -m app.seed
uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

환경 변수는 자동으로 `.env`에서 읽지 않는다. Compose만 `.env`를 읽으므로 Python 실행에는 export가 필요하다. URI 특수문자가 있는 비밀번호는 DATABASE_URL에서 percent encoding한다. PostgreSQL 호스트 포트는 POSTGRES_PORT로 변경하며 URL도 함께 맞춘다.

스키마 생성과 Seed는 명시적 명령이며 서버 시작 시 실행되지 않는다. create_all은 기존 스키마 변경을 처리하지 않는다. Seed는 고정 PK만 upsert하고 다른 데이터를 삭제하지 않는다. Seed 실행 당시 UTC를 기준으로 활성 구독은 30일 후 만료, 만료 구독은 50일 전 만료, 과거 성공 결제는 30일 전으로 생성한다. 재실행하면 같은 PK의 날짜도 새 기준 시각에 맞춰 갱신된다. 자동 테스트는 같은 생성 코드에 고정 기준 시각을 주입해 재현성을 유지한다.

## 조회 API

Swagger는 http://127.0.0.1:8000/docs 에서 확인한다. 모든 조회에 `Authorization: Bearer <token>`이 필요하다.

```bash
curl -H 'Authorization: Bearer replace-with-local-token' http://127.0.0.1:8000/organizations/00000000-0000-0000-0000-000000000001
```

| GET 경로 | 반환 |
|---|---|
| /users/{user_id} | 사용자 ID·조직·역할 |
| /organizations/{organization_id} | 조직·실제 팀원 수·제한 |
| /organizations/{organization_id}/subscription | 현재 구독 |
| /organizations/{organization_id}/payments | created_at DESC, id ASC의 items |

Seed 조직 끝자리 1은 정상, 2는 만료·제한 도달, 3은 past_due·결제 실패, 4는 구독·결제 부재다. 사용자 끝자리 101/102/103은 조직 1의 owner/admin/member, 201/301/401은 각 조직의 owner다.

토큰은 환경 JSON에서 사용자 UUID에 매핑하며 사용자 조직·역할은 DB에서 확인한다. 모든 역할은 같은 조직을 조회할 수 있다. 타 조직과 미존재는 같은 404, 구독 부재는 404, 결제 부재는 빈 items다. 인증 실패 401, UUID 검증 실패 422, DB 불가 503이다. 401/404/503은 `detail.code`와 `detail.message`, 422는 FastAPI 기본 검증 형식이다. 고객 조회 경로의 변경 메서드는 405다.

인증은 **로컬 개발 전용**이다. 토큰 만료·사용자 로그인·회전 기능이 없고 외부 공개용 인증을 제공하지 않는다. 내부 Service 호출도 인증 경계에서 생성한 CallerContext가 필요하다.

## PostgreSQL 테스트

개발 DB와 이름이 다른 전용 테스트 DB를 한 번 생성한다.

```bash
docker compose exec -T postgres createdb -U support support_test
uv run pytest -q
```

TEST_DATABASE_URL과 DATABASE_URL을 모두 설정해야 한다. 테스트는 매번 임의 이름의 전용 schema를 생성·제거하고 각 테스트 트랜잭션을 rollback한다. 개발 schema·데이터를 초기화하지 않는다. 이미 테스트 DB가 있다면 createdb는 반복하지 않는다.

## 문의와 동기 Agent 진단

```bash
curl -X POST http://127.0.0.1:8000/support/requests \
  -H 'Authorization: Bearer replace-with-local-token' \
  -H 'Content-Type: application/json' -d '{"content":"결제했는데 팀원을 초대할 수 없습니다"}'
# 응답 id를 request-id 자리에 넣는다.
curl -H 'Authorization: Bearer replace-with-local-token' http://127.0.0.1:8000/support/requests/request-id
curl -X POST -H 'Authorization: Bearer replace-with-local-token' http://127.0.0.1:8000/support/requests/request-id/diagnose
curl -H 'Authorization: Bearer replace-with-local-token' http://127.0.0.1:8000/support/requests/request-id/traces
```

content는 trim 후 1~4000자이며 추가 필드는 422다. 생성은 201/pending이고 생성자·조직은 인증으로 결정된다. 같은 조직의 모든 역할은 문의·결과·Trace를 조회하고 실행할 수 있다. 다른 조직과 미존재 문의는 같은 404다. 실행자의 역할과 관계없이 기본 진단 대상은 문의 생성자다.

진단 설정은 Python 프로세스에 환경 변수로 제공한다. 값은 Git이나 Trace에 저장하지 않는다.

```bash
export OPENAI_API_KEY='replace-with-api-key'
export OPENAI_MODEL='replace-with-function-calling-and-structured-output-model'
export AGENT_MAX_STEPS=8
export AGENT_MAX_TOOL_CALLS=16
export AGENT_TIMEOUT_SECONDS=60
```

Key/모델이 없어도 기존 조회와 문의 접수·조회는 동작한다. 이때 진단은 failed/llm_error로 저장된다. 모델은 Responses function calling과 JSON Schema를 지원해야 한다. SDK 재시도는 0이며 요청별 timeout은 남은 실행 시간과 20초 중 작은 값이다. DB 연결·풀·문장·lock 대기도 유한하며 종료 저장은 별도 5초 DB 문장 timeout을 적용한다. 총 HTTP 소요시간을 정확히 60초 이내로 보장하지 않는다.

pending → running → completed/failed를 동기적으로 처리한다. 200이면 `status`와 `diagnosis.outcome`을 확인한다. diagnosed는 원인·성공 관찰 근거, needs_information은 질문, escalate는 이관 안내다. 셋 모두 completed다. 처리된 기술 오류는 200/failed, diagnosis=null, 안전한 error_code로 반환한다. DB 실패는 503이며 종료 상태·진단·전체 Trace를 rollback하고 가능한 경우 실패 저장을 한 번 시도한다. completed 재호출은 실행 방식이 같으면 기존 결과를 반환하고 방식이 다르거나 running/failed이면 409/detail.code=conflict다. 실패 재실행은 새 문의를 생성한다.

Trace 응답은 `{items: [...]}`이며 sequence 순이다. 실행 중 Trace는 메모리에 있으므로 빈 배열일 수 있고 종료 시 일괄 저장한다. reasoning과 원본 SDK 응답은 저장하지 않는다. Trace의 usage는 공급자 미제공 시 null이다. 프로세스 종료나 지속 DB 장애는 running 잔존 및 메모리 Trace 유실을 일으킬 수 있다. 자동 복구는 제공하지 않는다.

정책은 `policies/{plans,permissions,troubleshooting}.json`에 있으며 실행당 한 번 읽는다. `search_policy` query 예시는 `초대 팀원 권한`, `결제 past_due`, `free pro enterprise`다. casefold·공백 토큰의 부분 문자열 일치를 합산하고 점수 내림차순/문서 ID 오름차순으로 최대 세 문서를 반환한다. 일치하지 않으면 빈 items다. 모든 플랜의 실제 팀원 한도는 조직 member_limit이며 실제 변경·환불·결제를 수행하지 않는다.

자동 테스트는 고정 Seed/UTC와 scripted Responses로 후속 관찰·근거·한도·권한·저장을 검증한다. 독립 PostgreSQL 연결로 실제 commit/rollback과 동시 claim도 검증한다. 실제 모델 진단 품질과 비용·성능 개선은 이 테스트로 입증하지 않는다. 실제 Smoke Test는 OPENAI_API_KEY와 OPENAI_MODEL을 제공한 개발 환경에서 위 복합 문의를 생성·진단하고 결과/Trace의 후속 Tool 선택과 근거를 확인한다. API 호출 비용이 발생한다.

## Workflow Baseline과 실행 방식

사용자가 실행하는 원커맨드 실제 API 검증은 [Workflow Smoke Test Rulebook](docs/workflow_smoke_test.md)을 따른다. `uv run --env-file .env python scripts/workflow_smoke.py` 한 번으로 PostgreSQL 기동·테이블 생성·필요한 001 migration·Seed 갱신·임시 Backend 시작을 수행한다. 개발 DB에서만 실행하며 기존 Seed 수정값은 덮어쓸 수 있다. 종료 시 임시 Backend는 정리하고 PostgreSQL과 DB 기록은 유지한다. 비용 확인 후에만 진단하며 보고서는 `.smoke-results/workflow-run-*/report.md`에 저장된다.

`POST /support/requests/{id}/diagnose?mode=workflow`로 Fixed-step Workflow를 선택한다. `mode=agent` 또는 생략은 기존 Agent이며 다른 값은 422다. 생성 body는 계속 `content`만 받는다. 문의 응답의 `execution_mode`는 pending에서 null, 실행 시작 이후에는 agent/workflow다.

Workflow는 문의 생성자 → 인증된 조직 → 구독 → 결제 이력 → 정책 검색 `플랜`, `권한`, `구독 결제` 순서로 일곱 번 조회한다. 같은 Service/Tool Adapter가 반환한 관찰을 단일 LLM 요청에 전달하며 Tool 정의·추가 조회·모델 재시도는 제공하지 않는다. 부재/빈 결과는 계속하고 기술 오류는 중단한다. 최종 Diagnosis와 Evidence 검증·상태 전이·종료 저장은 두 방식 모두 Application이 담당한다. `WORKFLOW_TIMEOUT_SECONDS`는 기본 60초인 양의 유한 값이다. Budget 소진 시 추가 작업을 멈추고 LLM I/O를 취소하지만, 동기 DB와 종료 저장의 별도 timeout 때문에 전체 HTTP 응답의 엄격한 60초 상한은 보장하지 않는다.

문의는 방식에 관계없이 한 번만 실행한다. running/failed 재호출은 409, completed는 저장된 방식과 같을 때 결과를 재사용하며 다르면 409다. 따라서 workflow 완료 후 mode를 생략해도 409다. 비교 시 같은 생성자·내용의 문의를 방식별로 따로 생성한다.

### 기존 DB 이행

빈 신규 DB는 기존 `uv run --env-file .env python -m app.db`로 초기화한다. `create_all()`은 기존 테이블에 컬럼을 추가하지 않으므로 기존 DB에는 다음 절차가 필요하다.

1. 앱과 진단 요청을 중지하고 기존 DB를 백업한다.
2. 저장소 루트에서 `docker compose exec -T postgres psql -U support -d support_dev -v ON_ERROR_STOP=1 < migrations/001_workflow_execution_mode.sql`을 한 번 실행한다. DB 이름이 다르면 실제 개발 DB 이름을 사용한다. SQL은 트랜잭션 안에서 컬럼 추가·backfill·상태 제약 적용을 수행한다.
3. 기존 pending은 null, running/completed/failed는 agent로 기록되며 문의·진단·Trace·고객 데이터는 보존된다. 이행 검증 자동 테스트는 격리된 테스트 스키마에만 적용한다.
4. 새 앱을 시작한다. 자동 테스트는 `uv run --env-file .env pytest -q`로 전용 테스트 DB에서 실행한다. `.env`는 프로세스에 주입하며 내용을 조회·출력하지 않는다. 테스트는 Fake/Mock 또는 로컬 가짜 HTTP 서버를 사용하고 실제 OpenAI API를 호출하지 않는다.

롤백 시 앱을 중지하고 이전 코드로 되돌리되 컬럼·진단·Trace는 보존한다. 다만 이전 코드는 실행 방식 정보를 보여주지 않고 새 제약에 맞는 실행 방식 값을 쓰지 못하므로, 이전 코드로 진단 실행을 재개하려면 백업 후 `request_execution_mode` 제약을 별도 관리 작업으로 해제해야 한다. 컬럼 삭제는 롤백에 포함하지 않는다. 이행 SQL은 이미 이행된 DB에 재실행하지 않는다.

### 관찰값과 평가 한계

기존 Request/Trace에서 실행 방식·종료 상태·오류 유형, 실제 조회 및 LLM 시도 횟수, 관찰·정책 근거 참조를 확인할 수 있다. Token은 공급자가 제공한 input/output/total usage를 기록하며 미제공 값은 null이고 0으로 추정하지 않는다. 단계별 `duration_ms`, Request의 `finished_at - started_at` 실행 경과시간, 전체 HTTP 응답 지연은 서로 다르다. 단계 시간 합계는 준비·검증·저장 시간을 모두 포함하지 않는다. DB 지속 장애나 프로세스 종료 시 Trace 유실과 running 잔존이 가능하다.

모델 비용은 실제 적용 가격 정보로 별도 계산해야 한다. 진단의 의미적 정확성·정책 준수·안전성·근거 타당성은 구조적 참조 검증이나 Trace 존재만으로 보장되지 않는다. 조회 횟수 자체도 효율성·품질의 직접 증거가 아니며, 불필요한 조회인지 판단하려면 케이스별 필요 정보와 대조해야 한다. 실패율에는 평가 분모와 집계 기준이 필요하다.

이번 비교 대상은 현재 Fixed-step Baseline과 Agent 구현의 시스템 수준 Trade-off다. 모델 호출 수·Context·조회 범위도 다르므로 동적 Tool 선택만의 인과 효과나 Workflow/Agent 전체의 우열로 일반화하지 않는다. 동일 입력·권한·데이터·정책·모델·기준 시각의 검증 기반만 제공하며, 실제 평가 데이터셋·반복 실행·채점·통계·데이터 동결·비용 계산은 후속 Evaluation Change에서 구현한다.
