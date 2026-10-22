# Proposal

## Why

`backend-foundation`은 고객 상태를 안전하게 조회하는 기반을 제공하지만, 문의 내용과 조회 결과를 연결한 진단 기능은 없다. 이 기반을 재사용하는 Single Agent MVP를 추가하여 복합 문의의 진단, 근거 및 실행 기록을 API에서 확인할 수 있게 한다.

## What Changes

- 문의 생성, 동기 진단 실행, 결과 및 Trace 조회 API를 추가한다.
- SupportRequest, Diagnosis, AgentTrace를 저장하고 Application이 상태 전이와 트랜잭션을 소유한다.
- OpenAI Responses API로 Tool 선택 → 관찰 → 후속 판단 → 최종 결과의 Single Agent Loop를 구현한다.
- 기존 네 조회 Service에 얇은 읽기 전용 Tool 어댑터를 연결하고, 로컬 정책 문서 검색 `search_policy`를 추가한다.
- 플랜·권한·문제 해결 정책을 별도 문서로 관리하고 검색 결과와 고객 상태를 진단 근거로 남긴다.
- 성공, 정보 부족, 이관, 실행 한도, LLM/Tool 오류, 조직 격리, 저장 실패를 자동 테스트한다.
- 제외: 실제 결제·환불·계정 변경, Workflow Baseline, Evaluation 실행기, Multi-Agent, MCP, LangGraph, Redis, Celery, 비동기 작업 인프라, 범용 Agent Framework.

## Capabilities

### New Capabilities

- `support-requests`: 인증된 조직의 문의 접수·동기 실행·결과/Trace 조회, 저장 모델과 상태·트랜잭션 계약.
- `support-diagnosis`: Responses API Single Agent Loop, Service 기반 Tool, 근거가 연결된 결과, 종료 및 실패 계약.
- `policy-search`: 별도 관리하는 MVP 정책 문서와 재현 가능한 단순 검색.

### Modified Capabilities

없음. `customer-data`와 `customer-read-access`의 요구사항은 유지한다. 문의 저장은 별도 지원 도메인의 쓰기이며 기존 고객 조회 API와 Tool의 읽기 전용 계약을 변경하지 않는다.

## Impact

- 추가 예정: 문의 Router/Schema/Service/ORM, Agent Loop/Tool 어댑터, 정책 문서/검색 Service, 관련 pytest.
- 연결 변경 예정: `app/main.py`의 Router·오류 등록, `app/config.py`의 Agent 설정, 공통 Base 및 명시적 모델 등록, 공통 API 의존성의 최소 분리, DB 초기화와 테스트 fixture.
- 기존 `app/services/customer.py`의 조회 함수·DTO·인증 규칙과 Seed를 재사용한다. 기존 API와 테스트를 유지한다.
- OpenAI Python SDK 의존성과 환경 설정을 추가하되 API Key와 모델은 코드에 하드코딩하지 않는다. 자동 테스트는 실제 OpenAI 호출 없이 수행한다.
- PostgreSQL에 세 테이블을 추가한다. 기존 고객 테이블의 데이터·제약은 변경하지 않는다.
- 구현 기준은 이 Change의 명세이며 `docs/support-agent-design.md`는 이를 참조하는 검토 요약으로 작성한다. 이번 제안 작업에서는 코드와 테스트를 구현하지 않는다.
