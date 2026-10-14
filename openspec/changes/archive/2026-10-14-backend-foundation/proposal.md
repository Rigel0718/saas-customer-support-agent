# Proposal

## Why

Agent와 Workflow가 같은 고객 상태를 조회하려면 먼저 재현 가능한 데이터와 조직 접근 경계를 갖춘 Backend가 필요하다. MVP Week 1의 공통 조회 기반을 구축해 이후 진단 로직의 데이터 접근과 비즈니스 로직 중복을 방지한다.

## What Changes

- FastAPI 프로젝트 구조와 환경 설정, PostgreSQL 및 SQLAlchemy 연결을 정의한다.
- User, Organization, Subscription, Payment 모델과 관계·제약을 구현 대상으로 정한다.
- 정상 상태와 구독 만료·결제 실패·권한 부족·팀원 제한 상태의 재현 가능한 Seed를 제공한다.
- 호출자 조직 범위를 강제하는 읽기 전용 Service와 API를 제공한다.
- pytest로 조회 결과, 데이터 일관성, 주요 오류와 조직 간 격리를 검증한다.
- 제외: Agent, Workflow 실행, LLM, MCP, Evaluation, 정책 검색, 문의·Trace 저장, 데이터 변경 API, 실제 결제, Redis, Celery, 배포 환경 구축.
- 최초 propose 단계는 계획 문서만 생성했으며, 이후 명시적인 apply 요청에 따라 아래 범위를 구현한다.

## Capabilities

### New Capabilities

- `customer-data`: 고객 지원용 도메인 데이터, 관계 제약과 재현 가능한 Seed.
- `customer-read-access`: 호출자 인증 컨텍스트, 조직 경계, 공통 읽기 Service 및 HTTP 조회 계약.

### Modified Capabilities

없음. 기존 main spec이 없는 첫 Change다.

## Impact

향후 `app/`, `tests/`, `pyproject.toml`, `uv.lock` 및 로컬 PostgreSQL용 Compose와 환경 설정 예제가 추가된다. Python 3.11+, uv, FastAPI, Pydantic v2, SQLAlchemy, PostgreSQL 드라이버, pytest를 사용한다. 기존 서비스나 API에 대한 호환성 변경은 없다. 로컬 인증은 환경 설정 토큰을 DB 사용자에 매핑하는 최소 방식이며 운영 인증을 대신하지 않는다.
