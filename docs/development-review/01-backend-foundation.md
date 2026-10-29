# 01. Backend Foundation — 비교 실험의 데이터·권한 기반

[전체 흐름](README.md) · 다음: [Support Agent](02-support-agent.md)

## Context — 진단 전에 공통 조회 기반부터

Issue#1 당시에는 서비스 코드 없이 MVP·SDD 문서와 OpenSpec 설정만 있었다. Agent와 Workflow에 각각 조회와 인가를 구현하면 비교 대상의 데이터 의미부터 달라진다. [Proposal](../../openspec/changes/archive/2026-10-14-backend-foundation/proposal.md)은 정상·문제 상태를 재현하고, HTTP와 후속 Tool이 같은 조직 경계와 조회 규칙을 사용하도록 Backend를 첫 변경으로 정했다.

## Requirements — 상태를 제공하되 진단하지 않는다

[customer-data](../../openspec/changes/archive/2026-10-14-backend-foundation/specs/customer-data/spec.md)는 User·Organization·현재 Subscription·Payment의 관계와 무결성, 재실행 가능한 Seed를 요구했다. [customer-read-access](../../openspec/changes/archive/2026-10-14-backend-foundation/specs/customer-read-access/spec.md)는 인증된 조직 안의 읽기, 타 조직/미존재의 동일 오류, 안정적 정렬과 데이터 불변성을 요구했다.

구독의 `status`는 저장값이며 현재 시각으로 재분류하지 않는다. 초대 가능 여부, 결제 실패의 원인 진단, 정책 검색, 문의·Trace 저장은 이 단계의 비범위였다. 같은 조직의 owner/admin/member 모두 최소 고객 정보를 읽을 수 있고 전역 관리자 권한은 없다.

## Design — 작은 동기식 구조와 강제되는 접근 범위

[Design](../../openspec/changes/archive/2026-10-14-backend-foundation/design.md)은 FastAPI → Service → SQLAlchemy → PostgreSQL 구조를 채택했다. 당시 문서에 명시된 대안은 비동기 드라이버·범용 Repository이며, 조회 규모에 비해 복잡성이 커서 제외했다.

| 경계 | 실제 책임과 설계 효과 |
|---|---|
| [API/dependencies](../../app/api/dependencies.py), [routes](../../app/api/routes.py) | Bearer 토큰을 설정의 user UUID에 매핑하고 DB 사용자로 컨텍스트 구성. 입력·DTO·HTTP 연결 담당 |
| [Customer Service](../../app/services/customer.py) | `authenticate`, frozen `CallerContext`, 조직 검사, 조회·정렬, DTO와 `CustomerError`. HTTP와 LLM에 의존하지 않음 |
| [ORM](../../app/models/customer.py) | FK·UNIQUE·CHECK로 관계와 허용 상태를 강제. 잘못된 데이터와 유효한 업무 오류 상태를 구별 |
| [DB](../../app/db.py) | PostgreSQL/UTC, 유한 연결·SQL timeout, 요청별 Session 종료·예외 rollback. 정상 종료의 자동 commit은 없음 |

토큰은 로컬 개발용 불투명 값이며 JWT/OAuth·로그인·만료/회전 시스템은 구현하지 않았다. Service가 받는 컨텍스트는 신뢰된 Python 경계에서 주입한다. `CallerContext` 타입 검사 자체가 인증 증명은 아니므로 Tool 인자로 컨텍스트를 구성해서는 안 된다.

## Implementation — 모델의 의미와 읽기 계약

| 데이터 | 구현한 관계·제약 | 후속 진단에서 갖는 의미 |
|---|---|---|
| Organization | `member_limit >= 0`, User 1:N | `member_count`는 별도 컬럼 없이 `get_organization`의 User COUNT. 한도와 현재 인원이 이중 저장되지 않음 |
| User | 조직 FK, owner/admin/member CHECK | 문의 당사자의 역할을 관찰할 수 있음. 실행자의 역할과 구별 |
| Subscription | 조직 FK·UNIQUE, plan/status CHECK, nullable `expires_at` | 조직마다 최대 하나의 현재 스냅샷. 상태/시각 불일치를 숨기지 않고 반환 |
| Payment | 조직 FK, status CHECK, succeeded에만 `paid_at` 필수 | 성공·실패·대기 이력은 데이터다. `created_at DESC, id ASC`로 동일 시각도 안정적으로 정렬 |

모든 ID는 UUID, 시각은 timezone-aware다. 참조된 조직 삭제는 RESTRICT한다. 금액·PG·환불·구독 이력 모델은 요구 범위 밖이었다.

`get_user`는 `id AND organization_id`로 조회한다. 조직·구독·결제는 `require_organization`이 대상 조직과 컨텍스트 조직을 먼저 비교한다. 다른 조직과 없는 ID는 모두 `not_found`이며 API에서 404다. 구독 부재는 404, 빈 결제 이력은 `items=[]`, expired/past_due/failed/pending은 정상 데이터로 200이다. 인증 실패 401, 입력 오류 422, DB 오류 503의 HTTP 변환은 [app/main.py](../../app/main.py)에 있다.

초기 스키마는 명시적 `metadata.create_all`, Seed는 [apply_seed](../../app/seed.py)의 PostgreSQL upsert다. 호출자가 트랜잭션을 소유하며 고정 PK만 삽입/갱신하고 비-Seed 레코드는 삭제하지 않는다. API 시작 때 자동 실행하지 않는다. Agent 도입 후 공통 [Base](../../app/models/base.py)·[모델 등록](../../app/models/__init__.py)으로 옮겼지만 고객 모델과 Service 계약은 재사용했다.

### 이후 변경 — 절대 날짜에서 상대 날짜로

최초 Archive는 고정 UTC 날짜와 실행 시각에 무관한 동일 값이라는 계약이었다. 현재 [customer-data main spec](../../openspec/specs/customer-data/spec.md)과 `build_manifest(reference_time)`는 활성 만료일 +30일, 만료 구독 -50일, 과거 결제 -30일을 사용한다. `apply_seed` 기본값은 실행 당시 UTC다.

Git `b0ac208`(2026-10-22, Agent Smoke 추가)에서 코드·main spec·테스트가 함께 바뀐 것이 확인된다. 고정 과거 날짜가 시간이 지나며 실제 Smoke의 활성 상태와 어긋나는 문제를 피하는 효과가 있다. 다만 이 이유가 Commit 메시지에 별도로 설명된 것은 아니므로 변경 효과에 대한 해석으로 구분한다. 독립된 Seed 변경 Change는 확인하지 못했다. “같은 기준 시각으로 반복하면 동일”과 “다른 기준 시각이면 날짜 갱신”이 현재 재현성 계약이다.

## Verification — 요구사항에서 테스트까지

| 요구사항 | Spec 근거 | 구현 위치 | 검증 근거 |
|---|---|---|---|
| 관계·조회 필드·무결성 | [customer-data](../../openspec/specs/customer-data/spec.md)의 관계/무결성 | ORM 네 모델, `get_organization` | [test_backend.py](../../tests/test_backend.py): `test_constraints`, `test_restrict_organization_delete`, `test_api_and_services_success_read_only` |
| 조직 범위·위조 방지 | [customer-read-access](../../openspec/specs/customer-read-access/spec.md)의 인증/공통 강제 | `get_context`, `get_user`, `require_organization` | 같은 파일: `test_authentication_failures`, `test_cross_org_missing_and_header_spoof` — HTTP와 Service 직접 호출 검사 |
| 부재·정렬·오류 보호 | 같은 Spec의 API/오류 계약 | 네 조회 함수, `main` 오류 Handler | `test_missing_billing`, `test_business_errors_return_data`, `test_database_failure_sanitized` |
| 재현성·비소유 보존·rollback | 현재 customer-data의 Seed 요구 | `build_manifest`, `apply_seed` | 같은 파일의 Seed 테스트 및 [test_seed.py](../../tests/test_seed.py): `test_seed_dates_are_relative_to_reference_time` |
| 읽기 불변성 | customer-read-access의 읽기 전용성 | GET API와 공통 Service | `test_api_and_services_success_read_only`, `test_write_methods_rejected` |

[tests/conftest.py](../../tests/conftest.py)는 개발 DB와 이름이 다른 PostgreSQL DB에 테스트 소유 schema를 만들고 일반 테스트를 savepoint/rollback으로 격리한다. SQLite 대체로 FK·CHECK·UUID 차이를 숨기지 않는 선택이다. Seed 전용 트랜잭션 실패도 테스트한다.

[당시 설계 요약](../backend-foundation-design.md)의 실행 기록은 PostgreSQL 16에서 **69 passed, 1 warning**, 초기화·Seed 두 번·실제 조직 조회 HTTP 200/member_count=3이다. 이번 작업에서 재실행한 결과가 아니며 현재 전체 테스트 수로 사용하지 않는다. 주요 구현 Commit은 `4212241`, 패키지 정리는 `e8782dc`, 단계 완료 정리는 `d361dfd`다.

## Outcome — 진단이 붙을 수 있는 기반

인증된 조직 안의 동일한 상태·정렬·오류 계약을 HTTP와 Python 호출에서 재사용할 수 있게 됐다. Service가 사실을 제공하고 정책·원인 판단은 다음 단계에 남겼기 때문에 Agent Tool과 Workflow가 별도 데이터 규칙을 만들 필요가 없었다.

운영 인증, pagination, 자동 스키마 migration은 완료 범위가 아니다. Seed가 모든 평가 Case의 GT를 대신하지도 않는다. 후속 Evaluation은 별도 Fixture를 구성하면서 이 Service의 권한과 데이터 의미를 재사용했다.
