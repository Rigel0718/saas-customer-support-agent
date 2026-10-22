# Tasks

아래는 별도 apply 요청으로 수행할 구현 작업이다. 이번 문서 수정에서는 실행하지 않는다. 설계의 미결정 Evaluation 항목은 이 작업 목록에 포함하지 않는다.

검증 시 테스트 환경변수는 기존 프로젝트의 `.env` 주입 방식을 따른다. Python은 `.env`를 자동 로딩하지 않으므로 테스트 프로세스에 로딩·export하여 전달하며, Codex는 `.env` 내용을 직접 조회하거나 출력하지 않는다. 설정 이름·구조는 `.env.example`을 기준으로 확인한다. 실제 OpenAI API 호출은 수행하지 않고 Fake/Mock을 사용하며 실제 API Key를 요구하지 않는다.

## 1. 실행 방식 저장과 기존 DB 호환

- [x] 1.1 SupportRequest와 RequestDTO에 nullable execution_mode(agent/workflow)를 추가하고 pending/null 및 실행 상태/유효 방식 제약을 반영한다. 모델/DTO 테스트로 잘못된 방식·상태 조합 거부와 기존 결과 필드 보존을 검증한다.
- [x] 1.2 기존 DB용 실행 방식 컬럼 추가·backfill·제약 적용 SQL을 제공한다. 변경 전 스키마의 네 상태별 문의·진단·Trace fixture에 적용하는 테스트로 pending=null, 나머지=agent와 관계/내용 보존을 검증한다. 개발 DB에는 테스트를 적용하지 않는다.
- [x] 1.3 README에 신규 DB 초기화와 기존 DB 이행/앱 중지/롤백 절차를 구분해 기록한다. 제공 SQL·실제 모델·격리 DB 이행 테스트와 설명이 일치하는지 확인한다.

## 2. 고정 Workflow 실행과 단위 검증

- [x] 2.1 별도 Workflow 함수에 기존 어댑터/Service를 재사용한 네 고객 조회와 세 고정 정책 검색을 구현한다. 단위 테스트로 정확한 순서·검색어·생성자 대상·동일 조직 범위·정렬/필드 및 일곱 관찰을 검증한다.
- [x] 2.2 not_found/빈 결과는 계속하고 기술 오류는 중단하는 분기를 구현한다. 고정 fixture와 오류 주입으로 구독 부재·빈 결제/정책·past_due/failed/pending 및 정책 로딩/고객 조회 오류 후 추가 조회/LLM 호출이 없는지 검증한다.
- [x] 2.3 수집 관찰·sequence·문의·기준 UTC를 단일 Responses 입력으로 만들고 기존 DiagnosisDTO JSON Schema를 사용한다. Fake 요청 검사로 동일 모델 설정·Tool 정의 부재·최대 한 호출을 검증하고 거부/불완전/빈 출력/Tool 요청/통신 오류 분류와 재시도 부재를 검증한다.
- [x] 2.4 시작·조회·LLM·최종 검증·종료 Trace를 기존 형식으로 연결한다. 테스트로 순서·실제 관찰 참조·정책 ID·duration/model/response_id 및 Fake 공급자 usage의 보존과 누락 시 null, 오류의 비밀/내부 정보 비노출을 검증한다. 정상/조기 실패의 실제 조회·LLM 시도 수와 Request 실행 경과시간·Trace 단계별 시간을 주입 시계로 각각 확인하며 단계 합계를 전체 실행/HTTP 지연으로 취급하지 않는다.
- [x] 2.5 Settings와 .env.example에 WORKFLOW_TIMEOUT_SECONDS=60을 추가하고 유한 양수 검증을 구현한다. 기존 취소 가능한 I/O 헬퍼와 주입 시계로 조회 전후 Budget 소진·LLM I/O 취소·DB 별도 timeout·추가 작업 중단을 실제 OpenAI 통신 없이 검증한다.

## 3. 공통 Application 실행과 API 연결

- [x] 3.1 지원 Service의 기존 선점·검증·종료 저장 경로에서 agent/workflow를 선택하도록 최소 확장하고 방식은 running과 함께 확정한다. 별도 DB 세션으로 실행 중 방식 조회, 시작 commit 실패 시 미실행, 혼합 방식 동시 요청에서 한 실행만 시작함을 검증한다.
- [x] 3.2 기존 진단 API에 mode query enum과 기본 agent를 연결하고 execution_mode를 반환한다. API/OpenAPI 테스트로 mode 생략·workflow·잘못된 값·content-only 생성 계약·추가 응답 필드를 검증하고 기존 정확한 필드 집합 테스트를 갱신한다.
- [x] 3.3 running/failed 재호출과 completed의 방식 일치/불일치 처리를 구현한다. Agent/Workflow 양방향 조합 및 생략 재호출 테스트로 409/저장 결과 반환과 조회·LLM·Trace 중복 생성 부재를 검증한다.
- [x] 3.4 기존 최종 결과/근거 검증과 원자 저장을 Workflow에 적용한다. Fake로 세 outcome과 없는 Trace/정책 참조·근거 없는 diagnosed를 검증하고, 종료 commit 실패/지속 장애에서 503·rollback·실패 저장 한 번 시도·기존 완료 결과 보호를 durable DB 테스트로 확인한다.
- [x] 3.5 README의 지원 범위·mode 사용법·방식별 한 번 실행·관찰 가능한 항목과 D5의 측정/해석 한계를 갱신한다. 비교는 현재 Fixed-step Baseline과 Agent의 시스템 수준 Trade-off이며 일반적 우열이나 Tool 선택만의 인과 효과가 아님을 명시한다. 실제 API 호출 없이 OpenAPI와 Fake API 테스트로 문서의 요청/응답 계약을 확인한다.

## 4. E2E와 기존 동작의 통합 검증

- [x] 4.1 Fake Responses와 고정 Seed/정책/UTC를 사용한 Workflow E2E를 추가한다. 문의 생성 → workflow 진단 → 별도 요청/세션의 결과·Trace 조회까지 복합 문의·정보 부족·지원 범위 밖 결과와 고객 데이터 불변성을 검증한다.
- [x] 4.2 두 방식의 공통 조건을 통합 검증한다. 같은 내용·생성자의 별도 문의에 동일 조직·실행자 권한·고정 고객 데이터·정책 버전·기준 UTC를 적용하고 Fake 요청에서 동일 모델과 가능한 공통 생성 설정을 확인한다. 실제 조회 범위·Context·호출 횟수 차이는 보존하고 execution_mode·조회/LLM 시도 수·공급자 usage가 기록에서 확인되는지 검증한다. 같은 조직 admin 실행, 무인증, 타 조직/미존재 문의, 본문의 타 조직 ID·상태 변경 지시로 인증 범위 보존과 안전한 오류·LLM 미호출을 확인한다.
- [x] 4.3 위 환경변수 주입 후 전용 PostgreSQL 테스트 DB에서 `uv run pytest -q`로 Backend·정책·Agent Loop·deadline·지원 저장/API 및 새 Workflow 테스트를 실행한다. 기존/신규 테스트가 D4의 공통 조건과 방식별 실행 차이를 훼손하지 않는지 확인한다. 실제 API Key와 통신 없이 통과한 결과, 미실행/실패 항목과 알려진 한계를 보고한다. 실제 품질/비용/성능 비교는 수행하지 않는다.
