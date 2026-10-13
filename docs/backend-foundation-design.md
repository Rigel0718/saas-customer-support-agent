# Backend Foundation 설계 요약

상태: **Backend Foundation 구현 및 PostgreSQL 검증 완료**.
기준: [proposal](../openspec/changes/backend-foundation/proposal.md), [design](../openspec/changes/backend-foundation/design.md), [데이터 명세](../openspec/changes/backend-foundation/specs/customer-data/spec.md), [조회·접근 명세](../openspec/changes/backend-foundation/specs/customer-read-access/spec.md), [tasks](../openspec/changes/backend-foundation/tasks.md).

## 1. Change 개요

`backend-foundation`은 Agent와 Workflow가 공통으로 사용할 고객 지원 조회 기반을 만드는 첫 Change다. FastAPI, PostgreSQL 연결, 네 ORM 모델, 재현 가능한 Seed, 읽기 Service/API, 조직 격리와 pytest를 구현 대상으로 한다.

Agent·Workflow 실행, LLM, MCP, Evaluation, 정책 검색, 문의·Trace 저장, 데이터 변경 API, 실제 결제, Redis/Celery 및 배포는 제외한다. 구현은 `app/`, 자동 테스트는 `tests/`, 실행 안내는 `README.md`에 있다. uv 잠금 파일과 PostgreSQL 한 서비스의 Compose를 제공한다.

## 2. Architecture

`HTTP → 인증 의존성·CallerContext → 읽기 Service → SQLAlchemy Session → PostgreSQL` 구조다.

- FastAPI Handler: 입력 검증, 컨텍스트 전달, DTO 응답과 HTTP 오류 매핑.
- Application 인증 경계: 환경 토큰에 매핑된 DB 사용자를 확인하고 조직 범위를 결정.
- Service: 모든 조회의 조직 경계, 데이터 조회 및 HTTP에 독립적인 오류 관리.
- ORM/DB: 관계와 저장 무결성. Session은 요청 단위로 종료.
- Seed CLI: 수동 데이터 초기화. 조회 요청과 분리.

## 3. Data Model

| 모델 | 주요 필드 | 관계·설계 |
|---|---|---|
| User | id, organization_id, role | 조직에 N:1; owner/admin/member |
| Organization | id, name, member_limit | User·Payment 각각 1:N; member_count는 User COUNT로 계산 |
| Subscription | id, organization_id, plan, status, expires_at | 조직당 현재 구독 0..1; free/pro/enterprise |
| Payment | id, organization_id, status, paid_at, created_at | 조직 결제 이력; succeeded/failed/pending |

UUID·UTC 시각과 FK/UNIQUE/CHECK로 무결성을 강제한다. 업무 오류 상태는 저장 가능한 데이터로 유지한다. 구독 상태는 active/past_due/expired/canceled이며 만료일은 nullable이다.

## 4. API 및 Service Layer

| GET 경로 | 공통 Service | 역할 |
|---|---|---|
| /users/{user_id} | get_user | 같은 조직의 사용자·역할 조회 |
| /organizations/{organization_id} | get_organization | 조직·팀원 수·제한 조회 |
| /organizations/{organization_id}/subscription | get_subscription | 현재 구독 조회 |
| /organizations/{organization_id}/payments | get_payment_history | 정렬된 결제 이력 조회 |

Service는 신뢰된 CallerContext를 필수로 받는다. 다른 조직과 미존재는 같은 404, 구독 부재는 404, 결제 부재는 빈 items다. 401은 인증 실패, 422는 입력 오류, 503은 DB 불가에 사용한다. 조회로 상태를 갱신하거나 원인을 진단하지 않는다.

## 5. Design Decisions

확정한 설계안은 다음과 같다.

- 동기 SQLAlchemy·작은 모듈 구조: 초기 조회 기능에 필요한 복잡성만 유지한다.
- 환경 토큰→DB 사용자 매핑: 로컬 개발 인증의 최소 경계를 확보한다. JWT/OAuth는 이번 범위 밖이다.
- Service에서 조직 격리: HTTP 밖의 향후 Tool 호출에도 동일한 경계를 적용한다.
- 같은 조직의 모든 역할에 조회 허용: 역할은 진단 입력으로 제공하고 변경 권한 판단은 추가하지 않는다.
- 현재 구독 하나와 조직별 결제 이력: 이력용 구독 모델·PG 연동을 만들지 않는다.
- 팀원 수 계산: User 수와 저장된 count의 불일치를 피한다.
- 고정 UUID·UTC Seed와 안정적 결제 정렬: 반복 조회·테스트 결과를 재현한다.
- 실제 PostgreSQL 테스트와 명시적 create_all 초기화: DB 동작을 검증하되 첫 스키마에 migration 인프라를 추가하지 않는다.

## 6. Acceptance Criteria

다음 완료 조건을 실제 PostgreSQL 테스트와 서버 조회로 검증했다.

- 네 도메인을 API와 Service에서 조회하고 DTO·정렬·부재 계약을 충족한다.
- 정상·만료·결제 실패·권한 정보·제한 도달·pending·데이터 부재 Seed가 존재하며 두 번 적용해 동일한 결과를 얻는다.
- PostgreSQL에서 관계·열거값·결제 시각 제약과 Seed 비소유 데이터 보존을 검증한다.
- 모든 API 및 Service 직접 호출에서 조직 간 접근을 차단한다.
- 401/404/422/503과 변경 요청 405, 오류 정보 비노출, 조회 전후 데이터 불변성을 검증한다.
- 분리된 PostgreSQL 테스트 DB에서 spec의 모든 시나리오에 대응하는 pytest가 통과하고 문서의 실행 명령으로 조회가 가능하다.

검증 결과: PostgreSQL 16 컨테이너에서 `uv run pytest -q` **69 passed, 1 warning**. FK·UNIQUE·CHECK, Seed 재적용·비소유 데이터 보존·트랜잭션 rollback, 역할별 조회, 모든 교차 조직 접근, 오류 계약과 데이터 불변성을 확인했다. README의 초기화·Seed 두 번 적용·uvicorn 실행 후 조직 조회는 실제 HTTP 200과 member_count=3을 반환했다.

최초 샌드박스 실행은 로컬 DB 접근 제한으로 실패했으며 권한을 허용한 재실행은 통과했다. 경고는 Starlette TestClient의 httpx 사용 중단 예정 안내이며 테스트 실패는 아니다. 운영 인증·migration·Agent 기능은 검증 대상이 아니다.

## 7. 향후 연결 지점

Agent Tool과 Workflow는 인증 경계가 제공한 동일 CallerContext로 네 Service와 DTO를 재사용한다. Tool 인자의 ID로 호출자 컨텍스트를 만들지 않는다. Evaluation은 고정 Seed와 동일 초기 DB 상태를 활용할 수 있다. 정책 검색·진단 정답 데이터·평가 실행기는 이후 Change에서 설계한다.

## 8. 미결정 사항

명세나 작업 범위를 바꿀 필수 미결정 사항은 없다. 로컬 PostgreSQL 포트 55432와 분리된 support_dev/support_test DB로 검증했다. 다른 환경에서는 환경 변수로 조정한다.

검토 대상은 확정 설계안인 개발용 토큰 인증, 조직 내 모든 역할의 동일 조회 권한, 현재 구독 하나라는 모델이다. 외부 공개에 필요한 운영 인증 및 향후 스키마 migration은 이번 구현의 선행 조건이 아닌 후속 검토 사항이다.

`AGENTS.md`와 `openspec/config.yaml`의 문서 참조를 `docs/sdd_workflow.md`로 일치시켰다.

주요 설계 변경이나 Spec 충돌은 없다. 테스트 격리는 임의 schema와 테스트별 rollback으로 구체화했으며 개발 DB와 같은 이름은 호스트 별칭이 달라도 거부한다. DB 연결에 UTC timezone을 명시했다. 현재 범위의 미완료 작업은 없다. 운영 인증과 스키마 변경 migration은 후속 검토 사항이다.
