# Backend Foundation 독립 코드 리뷰

검토일: 2026-10-13. 대상: `backend-foundation`의 현재 작업 트리.

## 1. Review Summary

**최종 판단: READY — 현재 Backend Foundation은 다음 `support-agent` Change를 시작하기에 충분히 안정적이다.**

현재 명세 범위에서 재현 가능한 버그, 조직 격리 취약점, 데이터 손상, 중요한 Spec 위반 또는 진행을 막는 테스트 누락을 발견하지 않았다. P0~P3 Finding은 없다. 이는 운영 환경 전체의 안전성을 보장하는 판단이 아니라, 확정된 로컬 개발용 인증과 읽기 Backend의 범위에 대한 판단이다.

격리된 실제 PostgreSQL 16에서 기존 테스트 **69 passed, 1 warning**을 직접 확인했다. 별도 데이터 보존 검증을 추가한 뒤 실행한 전체 테스트도 **69 passed, 1 warning**이었다. 기존 API 테스트의 Session 대체가 가릴 수 있는 부분은 소스 파일을 수정하지 않는 일회성 실행으로 추가 확인했다. 실제 요청 Session, Uvicorn HTTP 조회, 연결 거부, 인증 및 Service 조회 실패, Seed CLI의 커밋과 재실행, 테스트 전후 기존 데이터 보존이 정상 동작했다.

리뷰 중 소스코드·테스트·OpenSpec 문서는 수정하지 않았다. 이 보고서만 새로 작성했다.

## 2. 검토한 코드 및 명세 범위

### 문서와 Git 기준

- `docs/mvp.md`, `AGENTS.md`, `docs/backend-foundation-design.md`.
- `openspec/changes/backend-foundation/proposal.md`, `design.md`, `tasks.md`.
- `specs/customer-data/spec.md`, `specs/customer-read-access/spec.md`의 **9개 Requirement와 19개 Scenario 전체**.
- 실행 안내와 환경 구성: `README.md`, `pyproject.toml`, `uv.lock`, `compose.yaml`, `.env.example`, `.gitignore`.

Git 이력에서 구현 커밋 `4212241`과 병합 커밋 `9cc0ca7`을 확인했다. `4212241`의 변경 파일 목록과 현재 작업 트리 diff를 검토했다. 리뷰 시작 시 이미 모델·DTO 구현을 `__init__.py`에서 `customer.py`로 옮긴 미커밋 변경이 있었다. 해당 변경도 리뷰 대상에 포함했다. 새 구현 파일의 내용은 HEAD의 기존 정의와 바이트 단위로 같았고, 나머지 소스·테스트 diff는 import 경로 변경이었다.

### 코드별 확인 사항

| 범위 | 주요 위치 | 확인 결과 |
|---|---|---|
| 환경 설정 | `app/config.py:15` | PostgreSQL 설정 및 비어 있지 않은 토큰→UUID 매핑을 검증한다. 토큰 설정 누락·오류에 무인증 fallback이 없다. |
| Application·HTTP 오류 | `app/main.py:20`, `:26`, `:42` | app factory, lifespan의 엔진 해제, 401/404/503 JSON과 Bearer challenge가 구현되어 있다. SQLAlchemy 예외를 정상 결과로 처리하지 않는다. |
| 요청 Session·인증 | `app/api/routes.py:17`, `:25` | 요청별 Session을 주입한다. 토큰에 매핑된 DB 사용자로 컨텍스트를 생성하며 대상 UUID와 조직 헤더는 인증 범위를 정하지 않는다. |
| 공통 Service | `app/services/customer.py:24`, `:29`, `:36`, `:44`, `:54`, `:60`, `:68` | 컨텍스트 없는 접근을 거부한다. User는 ID와 조직을 함께 조회하고 나머지는 먼저 조직 일치를 검사한다. 반환값은 DTO이며 HTTP에 의존하지 않는다. |
| ORM | `app/models/customer.py:12`, `:23`, `:32`, `:46` | UUID PK, 필수 열, FK 및 삭제 제한, 조직별 구독 UNIQUE, 열거값·음수 제한·결제 시각 CHECK가 명세와 일치한다. |
| DTO | `app/schemas/customer.py:8` | 조회 필드와 nullable 시각, 결제 items 계약을 제공한다. ORM 객체를 외부 계약으로 노출하지 않는다. |
| DB·트랜잭션 | `app/db.py:10`, `:17`, `:26`, `:35` | 연결 timezone은 UTC다. 예외 시 rollback 후 재발생시키고 Session을 닫는다. 개발 DB와 같은 이름의 테스트 DB를 거부한다. 스키마 생성은 별도 CLI다. |
| Seed | `app/seed.py:11`, `:18`, `:47`, `:57` | 고정 UUID·UTC 시각을 사용한다. manifest PK에만 upsert하고 CLI는 한 트랜잭션에서 커밋한다. 서버 시작 시 자동 Seed가 없다. |
| 테스트 격리 | `tests/conftest.py:18`, `:31`, `:49`, `:60` | 무작위 전용 schema와 제한된 search_path, 테스트별 외부 트랜잭션·savepoint를 사용한다. API 테스트는 요청 Session을 fixture Session으로 대체한다. |
| 동작·실패 테스트 | `tests/test_backend.py:27`~`:220` | DB 제약, Seed, 역할별 조회, 교차 조직 접근, 부재·입력·인증·DB 오류, 변경 메서드 차단, rollback을 검증한다. |

### 보안 경계에 대한 판단

`get_context()`는 설정의 토큰 매핑을 확인한 뒤 `authenticate()`로 DB 사용자를 조회한다. 인증된 A 조직의 컨텍스트와 B의 대상 ID를 함께 전달해도 네 Service 모두 데이터 반환을 거부한다. HTTP 응답에서 다른 조직의 대상과 미존재 대상은 동일한 404 본문이다. Service 직접 호출에서도 동일한 `not_found` 오류를 확인했다.

`CallerContext`는 생성 출처를 암호학적으로 증명하는 객체가 아니다. `require_context()`는 타입을 검사하고, 컨텍스트의 신뢰는 Application 호출 경계에 맡긴다. 이는 `design.md`에 명시된 결정이다. 신뢰된 내부 코드가 임의 컨텍스트를 만들 수 있다는 사실을 외부 요청자의 인증 우회 취약점으로 분류하지 않았다. 현재 HTTP 경로에는 외부 입력으로 컨텍스트를 생성하는 코드가 없다.

응답 오류는 고정 문자열이며 토큰·접속 문자열·SQL·스택을 넣지 않는다. 검토한 코드에서 실제 자격증명을 출력하는 경로를 발견하지 않았다. 실제 Uvicorn 검증에서 생성한 Bearer 토큰이 서버 출력에 포함되지 않는 것도 확인했다. 모든 종류의 외부 로깅 설정에 대한 검증까지 수행한 것은 아니다.

## 3. 테스트 실행 결과

### 환경과 실행 이력

리뷰 시작 시 `DATABASE_URL`, `TEST_DATABASE_URL`, `POSTGRES_PASSWORD`와 `.env`가 없었으며, 실행 중인 PostgreSQL 컨테이너도 없었다. 최초 전체 실행은 **3 passed, 66 setup errors**였고 원인은 필수 DB 환경 변수 누락이었다. 임시 DB를 구성한 뒤 샌드박스 내 실행 역시 로컬 연결 제한으로 같은 66개 fixture 초기화 오류가 발생했다. 이 두 결과를 구현 실패나 테스트 통과로 해석하지 않았다.

권한을 허용한 재실행은 다음처럼 기존 테스트를 변경하지 않고 수행했다.

```bash
DATABASE_URL=postgresql+psycopg://review@127.0.0.1:32768/foundation_review_dev \
TEST_DATABASE_URL=postgresql+psycopg://review@127.0.0.1:32768/foundation_review \
.venv/bin/python -m pytest -q -p no:cacheprovider --tb=short
```

**결과: 69 passed, 1 warning in 2.65s.**

`postgres:16` 로컬 이미지를 사용한 임시 컨테이너를 localhost의 임의 포트에 띄웠다. 기존 프로젝트 Compose 서비스와 영구 볼륨은 사용하지 않았다. 인증은 임시 컨테이너의 개발 전용 trust 설정을 사용했고 실제 Secret은 사용하지 않았다. `foundation_review_dev`는 테스트 fixture의 개발 DB 이름 비교용 설정이며 그 DB에 연결하거나 데이터를 생성하지 않았다. 모든 검증용 데이터는 `foundation_review`에만 존재했다. 검증 후 Uvicorn과 임시 컨테이너를 종료·제거했다.

경고 1건은 Starlette TestClient의 httpx 사용 중단 예정 안내다. 이번 테스트의 기능 실패로 분류하지 않았다.

### 기존 테스트 외에 직접 확인한 동작

다음은 저장소에 테스트 파일을 추가하지 않고 별도 Python 실행으로 확인했다.

| 검증 | 실제 결과 |
|---|---|
| `python -m app.db`, `python -m app.seed` 반복 실행 | 초기화 및 각 실행의 커밋 성공. 별도 연결로 읽은 네 테이블의 값·개수가 동일했다. |
| 비-Seed 데이터 보존 | 추가 조직과 결제 2건이 Seed 재실행 후에도 값·개수 그대로 보존됐다. |
| Session 대체 없는 실제 Application 요청 | owner/admin/member의 네 API 조회 성공. 역할별 교차 조직 접근·조직 헤더 위조를 차단하고 미존재와 동일한 404를 반환했다. |
| 인증과 업무 데이터 | 누락·잘못된 토큰·삭제된 사용자·잘못된 scheme·빈 Bearer를 401로 거부했다. 만료·past_due 상태와 구독 부재·빈 결제를 정상 계약대로 반환했다. |
| 동일 시각 결제의 역순 삽입 | 큰 UUID부터 삽입해도 created_at DESC, id ASC로 반환했다. 삽입 순서에 의존하지 않았다. |
| 인증 조회 및 Service 조회 예외 | Session의 `get` 및 `scalar`에서 OperationalError를 주입했을 때 내부 정보 없는 503을 반환했다. 이어지는 정상 요청은 성공했다. 요청 종료 후 checked-out 연결은 0이었다. |
| 실제 연결 거부 | 연결되지 않는 localhost 포트로 구성한 앱의 원래 요청 Session에서 내부 정보 없는 503을 반환했다. |
| 읽기 전후 불변성 | 새 연결의 테이블 스냅샷으로 HTTP 및 Service 직접 호출 전후의 값·개수가 동일함을 확인했다. |
| 기존 데이터가 있는 DB에서 전체 테스트 재실행 | **69 passed, 1 warning in 2.56s.** public schema의 Seed·추가 데이터는 그대로였고 테스트용 schema는 남지 않았다. |
| 실제 Uvicorn HTTP | app factory로 띄운 서버에 Bearer 인증 요청을 보내 **HTTP 200, member_count=3**을 확인했다. |
| 토큰 설정 없는 앱 시작 | 별도 프로세스의 시작이 실패했다. 실제 Uvicorn 출력에 검증용 토큰이 포함되지 않았다. |

### 검증 한계

- `docs/backend-foundation-design.md`에 적힌 과거 실행 자체를 소급 증명한 것은 아니다. 현재 작업 트리에서 동등한 핵심 동작과 전체 테스트 결과를 독립적으로 재확인했다.
- `uv sync --locked` 재설치와 README의 기존 Compose·개발 DB 명령을 그대로 실행하지 않았다. 설치된 `.venv`와 격리된 PostgreSQL을 사용했다. 초기화·Seed CLI와 Uvicorn factory의 실제 동작은 확인했다.
- 인증·Service 조회 실패는 주입으로 검증했고 연결 거부는 실제로 검증했다. 네트워크 중단, DB 재시작 중 진행 요청, 고부하·동시성 전체를 실험하지 않았다.
- 운영 인증, migration, Agent·Workflow·정책 검색·Evaluation 및 배포는 이번 Change의 범위 밖이다.
- 추가 확인은 일회성 검증이다. 저장소의 기존 API 테스트가 실제 요청 Session을 대체한다는 사실은 유지된다. 현재 동작은 추가 실행으로 확인했으며 이 이유만으로 결함을 분류하지 않았다.

## 4. Findings (우선순위순)

**Finding 없음.** P0, P1, P2, P3로 분류할 재현 가능한 문제를 발견하지 않았다.

현재 요구에 없는 운영 인증·pagination·migration 등을 누락으로 분류하지 않았다. 작은 패키지 구조나 구현 파일 분리도 스타일 문제로 분류하지 않았다. 위 검증 한계는 확인하지 못한 범위를 설명하며, 현재 구현에서 재현된 결함을 뜻하지 않는다.

## 5. Spec Compliance 요약

두 spec의 모든 Requirement와 Scenario에 대한 대응은 다음과 같다. 아래 테스트 이름은 `tests/test_backend.py`의 기존 테스트다. 별도 확인은 3절의 일회성 검증을 의미한다.

| 명세 Requirement | Scenario | 구현·검증 근거 | 판단 |
|---|---|---|---|
| customer-data: 핵심 도메인 관계 및 조회 필드 | 조직 데이터 관계 조회 | ORM 관계, DTO, `get_organization`의 User COUNT. `test_api_and_services_success_read_only`. | 충족 |
| 동일 Requirement | 구독 및 결제 부재 | 조직과 사용자를 유지한 채 구독 0..1·결제 0..N을 허용. `test_missing_billing`. | 충족 |
| customer-data: 데이터 무결성 | 잘못된 데이터 저장 거부 | PostgreSQL FK·UNIQUE·CHECK. `test_constraints`의 12개 잘못된 레코드와 원본 스냅샷 유지. | 충족 |
| 동일 Requirement | 업무 오류 상태 허용 | Seed의 expired·past_due·failed 저장 및 `test_business_errors_return_data`. | 충족 |
| customer-data: 재현 가능한 Seed | Seed 재실행 | PK 대상 upsert, 고정 manifest. `test_seed_reproducible_and_states` 및 별도 CLI 커밋·보존 확인. | 충족 |
| customer-data: 정상 및 문제 상태 fixture | 고정 상태 검증 | 정상·만료·실패·member·제한 도달·부재·pending 및 최소 두 조직. Seed 테스트와 업무·부재 조회 테스트. | 충족 |
| 동일 Requirement | 시간 재현 | 고정 UUID·timezone-aware UTC 상수, 현재 시각·난수에 의존하는 상태 계산 없음. Seed 테스트의 기준 시각·UTC assertion. | 충족 |
| customer-read-access: 호출자 인증 컨텍스트 | 인증 실패 | 토큰 매핑과 DB 사용자 확인. `test_authentication_failures`, `test_config_no_auth_fallback`, 별도 설정 누락 시작 실패. | 충족 |
| 동일 Requirement | 조직 범위 위조 | 대상 UUID·조직 헤더를 컨텍스트 생성에 사용하지 않음. `test_cross_org_missing_and_header_spoof` 및 원래 Session 검증. | 충족 |
| customer-read-access: 조직 경계의 공통 강제 | HTTP 교차 조직 접근 | 네 API의 동일 404. 교차 조직 테스트 및 원래 Session에서 세 역할 검증. | 충족 |
| 동일 Requirement | Service 직접 호출 | User 복합 조건 및 공통 조직 검사. 교차 조직 테스트와 별도 직접 호출 검증. | 충족 |
| 동일 Requirement | 컨텍스트 없는 조회 | `require_context`에서 unauthorized. 네 함수 모두 `None` 컨텍스트 거부 테스트. | 충족 |
| customer-read-access: 읽기 API 계약 | 동일 조직의 조회 성공 | 네 GET·DTO, 세 역할 동일 접근. `test_api_and_services_success_read_only`. | 충족 |
| 동일 Requirement | 결제 순서와 부재 | 두 정렬 키와 items. 성공·부재 테스트 및 동일 시각 역순 삽입 확인. | 충족 |
| 동일 Requirement | 구독 부재 | `get_subscription`의 not_found. `test_missing_billing`. | 충족 |
| customer-read-access: 입력 및 의존성 오류 계약 | 잘못된 식별자와 없는 대상 | FastAPI UUID 검증과 통일된 not_found. `test_invalid_uuid`, 교차 조직·미존재 비교 테스트. | 충족 |
| 동일 Requirement | DB 접근 실패 | SQLAlchemy 예외의 503 매핑. `test_database_failure_sanitized`와 별도 연결 거부·인증/조회 실패 확인. | 충족 |
| customer-read-access: 조회의 읽기 전용성 | 조회 전후 데이터 동일 | Service에 쓰기·진단 계산 없음. 성공·거부·변경 메서드 테스트와 새 연결 스냅샷 비교. | 충족 |
| 동일 Requirement | 변경 요청 차단 | 고객 경로에는 GET만 등록. `test_write_methods_rejected`의 네 경로×네 메서드. | 충족 |

설계에서 정한 구독 상태의 저장값 반환, 현재 구독 하나, 모든 역할의 동일 조직 조회, 전체 결제 이력 반환, 수동 create_all·Seed는 그대로 구현되어 있다. 범위를 확장한 Agent·MCP·추가 인프라는 없다.

`tasks.md`의 완료 표시에 대해, 1~4절의 구현과 테스트 근거를 확인했고 5.1의 핵심 초기화→Seed 반복→PostgreSQL 전체 테스트 순서도 재현했다. 5.2의 서버 조회는 실제 Uvicorn HTTP로 확인했다. README 환경을 그대로 재구성한 것은 아니므로 과거의 모든 실행 절차까지 검증했다고 주장하지 않는다. 현재 검토에서 완료 표시와 충돌하는 구현 누락이나 실패는 발견하지 않았다.

## 6. 다음 `support-agent` 구현에 미치는 영향

네 조회 Service는 Session·신뢰된 컨텍스트·대상 UUID를 받아 DTO를 반환하고 FastAPI에 의존하지 않는다. Agent Tool과 Workflow가 같은 함수를 호출할 수 있으며 현재 설계에 다음 Change를 막는 결함이나 불필요한 범용 계층은 없다.

다음 Change에서도 이미 합의된 경계를 유지해야 한다. Application이 인증한 `CallerContext`를 실행에 주입하고, LLM이 전달한 식별자는 조회 대상에만 사용한다. Tool 인자로 user_id·organization_id·role을 받아 호출자 컨텍스트를 생성하면 현재 검증한 경계가 유지되지 않는다. 이는 새로운 기능 제안이 아니라 기존 명세와 설계의 연결 조건이다.

읽기 Service의 Session 수명과 진단·Trace 저장의 트랜잭션은 다음 Change의 Application이 관리하면 된다. 현재 Backend를 먼저 재설계하거나 추상화할 필요는 없다. 추가 검증에서 확인한 실제 Session·연결 실패 경로를 이후 변경 시 회귀 확인 대상으로 활용할 수 있다.

## 7. 최종 판단 및 권장 조치

**READY. 바로 `support-agent` Change를 시작해도 된다.**

판단 근거는 명세 전체의 구현 대응, PostgreSQL의 무결성·rollback 테스트 통과, HTTP 및 Service 직접 호출의 조직 격리, 실제 앱 Session과 Uvicorn 조회 성공, Seed 재실행과 테스트의 데이터 보존이다. 다음 Change 전에 반드시 수정해야 할 항목은 없다.

이 판단은 현재 로컬 개발용 읽기 Backend 범위에 한정한다. 다음 Change에서 컨텍스트 주입·요청 수명·저장 트랜잭션을 연결할 때에도 동일한 보안 경계와 실제 PostgreSQL 검증을 유지한다.
