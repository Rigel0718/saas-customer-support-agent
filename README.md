# SaaS Customer Support Backend

Python 3.11+와 uv, Docker Compose가 필요하다. 현재 범위는 읽기 API와 개발용 인증이며 Agent·Workflow·Evaluation은 포함하지 않는다.

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

토큰은 환경 JSON에서 사용자 UUID에 매핑하며 사용자 조직·역할은 DB에서 확인한다. 모든 역할은 같은 조직을 조회할 수 있다. 타 조직과 미존재는 같은 404, 구독 부재는 404, 결제 부재는 빈 items다. 인증 실패 401, UUID 검증 실패 422, DB 불가 503이다. 401/404/503은 `detail.code`와 `detail.message`, 422는 FastAPI 기본 검증 형식이다. 변경 메서드는 405다.

인증은 **로컬 개발 전용**이다. 토큰 만료·사용자 로그인·회전 기능이 없고 외부 공개용 인증을 제공하지 않는다. 내부 Service 호출도 인증 경계에서 생성한 CallerContext가 필요하다.

## PostgreSQL 테스트

개발 DB와 이름이 다른 전용 테스트 DB를 한 번 생성한다.

```bash
docker compose exec -T postgres createdb -U support support_test
uv run pytest -q
```

TEST_DATABASE_URL과 DATABASE_URL을 모두 설정해야 한다. 테스트는 매번 임의 이름의 전용 schema를 생성·제거하고 각 테스트 트랜잭션을 rollback한다. 개발 schema·데이터를 초기화하지 않는다. 이미 테스트 DB가 있다면 createdb는 반복하지 않는다.
