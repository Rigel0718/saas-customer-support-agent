# Design

## Context

현재 저장소는 문서와 OpenSpec 설정만 있으며 재사용할 코드·테스트가 없다. 목적은 [proposal.md](proposal.md)를 따른다. 요구사항 기준은 `docs/mvp.md`, 실제 존재하는 `docs/sdd_workflow.md`, `AGENTS.md`다. `AGENTS.md`와 `openspec/config.yaml`의 참조를 실제 경로 `docs/sdd_workflow.md`로 일치시켰다.

## Goals / Non-Goals

**Goals:** HTTP와 향후 Tool이 같은 Service를 호출하고 같은 데이터 접근 경계·반환 값·오류 의미를 공유한다. PostgreSQL에서 관계 제약과 조회를 검증할 수 있는 작은 구조를 만든다.

**Non-Goals:** 고객 지원 원인 판단, 초대 허용 여부 계산, 플랜 정책 검색 및 운영용 인증 시스템. 구독 status는 저장 값을 반환하며 현재 시각으로 재분류하지 않는다. 역할은 조회 정보이며 초대·변경 권한 판정은 이후 Change에 속한다.

## Decisions

### 1. 작은 동기식 Application 구조

Python 3.11+, uv, FastAPI, Pydantic v2, SQLAlchemy 2.x 동기 Session과 psycopg를 사용한다. `app/main.py`는 app factory, `api/`는 라우터·의존성, `schemas/`는 입출력 DTO, `services/`는 범위 검증·조회·도메인 오류, `models/`는 ORM, `db.py`는 연결·Session, `config.py`는 환경 설정, `seed.py`는 명시적 초기화를 담당한다. `tests/`에 Service/API/DB 테스트를 둔다.

Handler는 입력·컨텍스트 주입·오류 HTTP 변환만 수행한다. Service는 Session과 신뢰된 컨텍스트를 받아 DTO를 반환하고 ORM 객체를 외부에 노출하지 않는다. 비동기 드라이버와 범용 Repository는 이 조회 규모에서 추가 복잡성이 커서 도입하지 않는다. Session은 요청별로 생성·종료하고 실패 시 rollback·close한다. DB 오류는 경계에서 서비스 불가로 변환한다.

### 2. 모델 관계와 제약

모든 id는 UUID이며 필수 필드는 아래와 같다. DB 연결 timezone을 UTC로 고정하고 시각은 timezone-aware UTC, 문자열 열거값은 DB CHECK로 제한한다.

| 모델 | 필드 | 관계·제약 |
|---|---|---|
| Organization | id, name, member_limit | member_limit >= 0; member_count는 User COUNT로 조회하여 중복 저장 방지 |
| User | id, organization_id, role | 조직 FK·인덱스; role=owner/admin/member |
| Subscription | id, organization_id, plan, status, expires_at | 조직 FK·UNIQUE; plan=free/pro/enterprise; status=active/past_due/expired/canceled; expires_at nullable |
| Payment | id, organization_id, status, paid_at, created_at | 조직 FK·인덱스; status=succeeded/failed/pending; created_at 필수, succeeded만 paid_at 필수이고 나머지는 null |

Organization 1:N User, 1:0..1 Subscription, 1:N Payment다. Subscription은 현재 스냅샷 하나이며 이력 모델을 만들지 않는다. Payment는 조직 결제 이력으로 직접 연결한다. 금액·PG 식별자·환불 필드는 이번 조회 요구에 필요하지 않아 제외한다. FK 대상 삭제는 제한한다. Payment 조회 DTO에는 정렬 근거인 created_at도 포함한다. status와 expires_at을 연동해 갱신하는 로직은 없다.

### 3. 최소 개발 인증과 조직 격리

명시적인 제품 인증 요구가 없으므로 로컬 개발 전용으로 환경 설정의 불투명 Bearer 토큰→user UUID 매핑을 사용한다. Secret은 코드나 Seed에 넣지 않으며 `.env.example`에는 자리표시자만 둔다. 토큰 설정 누락은 시작 시 설정 오류로 실패하고 무인증 fallback은 없다. 실제 토큰은 로그·응답에 노출하지 않는다. JWT/OAuth/로그인 API는 제외한다.

인증 의존성이 DB 사용자로부터 `CallerContext(user_id, organization_id, role)`를 구성한다. Service는 조직을 대상 ID로 바꾸지 않으며 User 조회에도 `id AND organization_id` 조건을 사용한다. 조직·구독·결제 조회는 먼저 대상 조직이 컨텍스트 조직인지 검증한다. HTTP를 우회한 내부 호출도 인증 경계에서 만든 컨텍스트를 필수로 전달한다. 임의 컨텍스트 생성은 인증된 행위가 아니며 향후 Tool은 이 객체를 LLM 입력에서 구성해서는 안 된다.

같은 조직의 모든 역할은 현재 최소 조회 필드를 읽는다. 전역 관리자 권한은 없다. 타 조직과 미존재는 같은 404로 숨긴다. 403으로 구분하면 존재 여부가 노출되므로 사용하지 않는다.

### 4. API와 Service 계약

| HTTP | Service 함수 | 결과 |
|---|---|---|
| GET /users/{user_id} | get_user(context, user_id) | User DTO |
| GET /organizations/{organization_id} | get_organization(context, organization_id) | Organization DTO 및 member_count |
| GET /organizations/{organization_id}/subscription | get_subscription(context, organization_id) | 현재 Subscription DTO; 부재 404 |
| GET /organizations/{organization_id}/payments | get_payment_history(context, organization_id) | `{items: [...]}`; 부재 빈 배열 |

각 Service는 HTTP에 의존하지 않는 오류를 발생시키고 Handler가 401/404/503으로 매핑한다. 정상·만료·실패는 업무 데이터이므로 200으로 반환한다. 결제는 created_at DESC, id ASC로 안정적으로 정렬한다. MVP의 작은 고정 데이터에 전체 조회를 사용하며 pagination·필터·집계 진단을 추가하지 않는다. `/support/requests`는 이후 Change에서 구현한다.

### 5. 재현 가능한 Seed와 초기 스키마

고정 UUID·UTC 시각·고정 기준 시각을 가진 fixture manifest로 정상, 만료, 결제 실패/past_due, member 역할, 제한 도달, 데이터 부재, pending을 구성한다. 복합 진단 정답이나 Evaluation 30개 케이스는 만들지 않는다. 동일 시각 결제와 과거 성공 후 실패 결제를 포함한다. 모든 역할과 최소 두 조직을 포함한다.

Seed는 별도 CLI에서 트랜잭션 단위로 고정 PK를 삽입/갱신하고 Seed 소유가 아닌 데이터는 삭제하지 않는다. 기본 수동 실행이며 API 시작 시 자동 Seed는 없다. 비-Seed 사용자로 인해 member_count가 달라질 수 있으므로 기준 스냅샷 검증은 빈 전용 DB에서 수행한다.

초기 스키마 생성은 명시적 개발 초기화 명령에서 metadata.create_all을 사용한다. 변경 이력이 없는 첫 모델에 Alembic을 추가하지 않는다. create_all은 기존 테이블 변경을 처리하지 않으며 향후 스키마 변경 시 migration 도구 도입을 별도 검토한다. Compose는 로컬 PostgreSQL 한 서비스만 제공한다.

### 6. PostgreSQL 기반 검증

SQLite 대체 테스트는 FK·CHECK·UUID·시각 처리 차이를 숨길 수 있으므로 PostgreSQL을 사용한다. 별도 TEST_DATABASE_URL을 필수로 하고 개발 DB와 같은 대상이면 초기화를 거부한다. 테스트 DB의 격리 schema/트랜잭션 fixture로 초기화·rollback하고 개발 데이터는 변경하지 않는다. 인증 토큰은 테스트 fixture에서만 주입한다.

완료 기준은 두 spec의 모든 시나리오에 대응하는 pytest 통과다. DB 제약, Seed 두 번 적용, UTC·정렬, 네 API 성공/401/404/422/503, 역할별 동일 조직 조회, 모든 교차 조직 조회, Service 직접 호출 및 읽기 전후 불변성을 포함한다. DB 장애는 의존성 실패 주입으로 검증하고 정상 조회·무결성은 실제 PostgreSQL에서 검증한다. 구현 단계에서 PostgreSQL 테스트로 검증한다.

## Risks / Trade-offs

- 개발 토큰은 사용자 세션·만료·회전을 제공하지 않음 → 로컬 개발 전용으로 명시하고 외부 공개 전 운영 인증 별도 설계.
- 컨텍스트를 내부에서 위조하면 경계가 약화됨 → 인증 의존성에서만 구성하며 향후 Tool은 Application 컨텍스트를 주입.
- 고정 시각이 실제 현재 시각과 다름 → 저장 상태 조회와 시간 판정을 분리하고 테스트 기준 시각을 고정.
- 전체 결제 조회 및 create_all은 확장에 한계 → 현재 Seed 규모에 한정하며 데이터 증가·스키마 변경이 발생하면 별도 Change 검토.

## Migration Plan

현재 배포·데이터가 없어 이관은 없다. 구현 단계에서 의존성 잠금, 로컬 DB 설정, 명시적 스키마 초기화, Seed, 테스트 순서로 확인한다. 롤백은 변경 코드 제거와 전용 개발 DB 재생성이며 기존·운영 DB 삭제를 자동화하지 않는다. 배포 작업은 이번 범위에 없다.

## Open Questions

명세를 바꿀 필수 미결정 사항은 없다. 구현 전에 로컬 PostgreSQL 호스트 포트와 개발·테스트 DB 이름의 충돌 여부를 확인한다. 이는 환경별 설정으로 처리하고 API·모델 계약에는 영향을 주지 않는다. 개발 토큰·조직 내 동일 조회 권한·현재 구독 하나라는 선택은 확정 설계안이며 사용자 검토 대상이다.
