# SaaS Customer Support Backend

Python 3.11+와 uv, Docker Compose가 필요하다. 현재 범위는 고객 읽기 API, 개발용 인증과 support-agent MVP다. Workflow Baseline·Evaluation은 다음 Change에서 구현한다.

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

스키마 생성과 Seed는 명시적 명령이며 서버 시작 시 실행되지 않는다. create_all은 기존 스키마 변경을 처리하지 않는다. Seed는 고정 PK만 upsert하고 다른 데이터를 삭제하지 않는다. 기준 시각은 `2026-01-15T00:00:00Z`이며 상태를 현재 시각으로 재계산하지 않는다.

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

pending → running → completed/failed를 동기적으로 처리한다. 200이면 `status`와 `diagnosis.outcome`을 확인한다. diagnosed는 원인·성공 관찰 근거, needs_information은 질문, escalate는 이관 안내다. 셋 모두 completed다. 처리된 기술 오류는 200/failed, diagnosis=null, 안전한 error_code로 반환한다. DB 실패는 503이며 종료 상태·진단·전체 Trace를 rollback하고 가능한 경우 실패 저장을 한 번 시도한다. completed 재호출은 기존 결과를 반환하고 running/failed 재호출은 409/detail.code=conflict다. 실패 재실행은 새 문의를 생성한다.

Trace 응답은 `{items: [...]}`이며 sequence 순이다. 실행 중 Trace는 메모리에 있으므로 빈 배열일 수 있고 종료 시 일괄 저장한다. reasoning과 원본 SDK 응답은 저장하지 않는다. Trace의 usage는 공급자 미제공 시 null이다. 프로세스 종료나 지속 DB 장애는 running 잔존 및 메모리 Trace 유실을 일으킬 수 있다. 자동 복구는 제공하지 않는다.

정책은 `policies/{plans,permissions,troubleshooting}.json`에 있으며 실행당 한 번 읽는다. `search_policy` query 예시는 `초대 팀원 권한`, `결제 past_due`, `free pro enterprise`다. casefold·공백 토큰의 부분 문자열 일치를 합산하고 점수 내림차순/문서 ID 오름차순으로 최대 세 문서를 반환한다. 일치하지 않으면 빈 items다. 모든 플랜의 실제 팀원 한도는 조직 member_limit이며 실제 변경·환불·결제를 수행하지 않는다.

자동 테스트는 고정 Seed/UTC와 scripted Responses로 후속 관찰·근거·한도·권한·저장을 검증한다. 독립 PostgreSQL 연결로 실제 commit/rollback과 동시 claim도 검증한다. 실제 모델 진단 품질과 비용·성능 개선은 이 테스트로 입증하지 않는다. 실제 Smoke Test는 OPENAI_API_KEY와 OPENAI_MODEL을 제공한 개발 환경에서 위 복합 문의를 생성·진단하고 결과/Trace의 후속 Tool 선택과 근거를 확인한다. API 호출 비용이 발생한다.
