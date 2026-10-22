# Tasks

이 목록은 구현 및 검증 진행 상태를 기록한다. 계약은 세 capability spec, 구현 경계는 design.md D1~D8을 따른다.

## 1. 공통 모델과 저장 기반

- [x] 1.1 Base만 `app/models/base.py`로 분리하고 customer/support 모델의 명시적 패키지 등록을 연결한다. DB CLI와 fixture가 같은 진입점을 사용하며 Router import 없이 일곱 테이블이 등록되는 테스트와 기존 Base import 호환성으로 검증한다.
- [x] 1.2 SupportRequest/Diagnosis/AgentTrace 모델과 FK·상태/outcome CHECK·문의별 진단 UNIQUE·Trace sequence UNIQUE/양수 제약을 구현한다. PostgreSQL 정상 저장과 잘못된 참조·열거값·중복 저장 거부 테스트로 검증한다.
- [x] 1.3 문의·진단·근거·Trace Pydantic Schema를 추가한다. content trim/길이/추가 필드, outcome별 필수 내용, UTC 직렬화 및 nullable 필드 테스트로 검증한다.

## 2. 인증 재사용과 문의 API

- [x] 2.1 `api/dependencies.py`로 기존 인증·Session 의존성만 분리하여 두 Router와 테스트가 동일 객체를 참조하도록 연결한다. 기존 인증·의존성 override·고객 API 테스트를 실행하여 동작 보존을 검증한다.
- [x] 2.2 Support Service의 문의 생성·조직 범위 조회와 생성 commit/rollback, 지원 Router의 POST/GET 문의 및 GET traces를 구현한다. 201→독립 Session 재조회, 422/401/404, 동일 조직 각 역할, 조직 헤더 위조 및 생성 저장 실패 503 테스트로 검증한다.
- [x] 2.3 기존 main에 지원 Router와 409 오류 계약을 연결한다. 고객 401/404/503 형식 유지와 지원 endpoint의 OpenAPI/응답 필드를 테스트하고 README에 생성·조회 예시를 추가한다.

## 3. 정책 문서와 검색

- [x] 3.1 `policies/`에 플랜·권한·문제 해결 JSON 문서를 작성한다. 고정 document_id/title/keywords/content, 기존 상태값, member_limit 기반 제한 및 D7의 초대·결제 규칙을 문서 fixture 검증과 개발자 검토로 확인한다.
- [x] 3.2 고정 문서 집합을 읽는 단순 키워드 검색 Service를 구현한다. 최대 3개, 점수/ID 정렬, 한국어·영문 키워드, 빈 결과, 잘못된 query, 파일 로딩 실패를 테스트하고 검색 입력 예시를 README에 기록한다.

## 4. 읽기 전용 Tool 연결

- [x] 4.1 다섯 Tool의 고정 정의·strict Schema·Pydantic 인자 검증·DTO/관찰 직렬화를 추가하고 기존 customer 조회 함수를 직접 호출한다. 정상 필드·결제 정렬·Service 호출 인자와 고객 데이터 snapshot 동일성으로 검증한다.
- [x] 4.2 인증으로 받은 CallerContext/Session만 주입하고 문의 생성자를 기본 대상으로 연결한다. 타 조직/없는 대상의 동일 not_found, 같은 조직의 다른 실행자, context/role 추가 인자 및 공격 본문 테스트로 경계를 검증한다.
- [x] 4.3 안전한 조회 오류 관찰과 치명적 실행 오류를 구분한다. invalid JSON/UUID/unknown_tool, 구독 부재·빈 결제, DB/내부 오류, raw 오류·Secret 비노출 테스트로 검증한다.

## 5. Responses Single Agent Loop

- [x] 5.1 OpenAI SDK 의존성·환경 설정·최소 Responses 호출 주입 지점을 추가한다. 기존 Settings 생성 및 Key/모델 없는 Backend 시작이 유지되는 테스트, 진단 설정 누락 실패 테스트와 `uv sync`로 검증하고 환경 변수 안내를 README에 추가한다.
- [x] 5.2 response.output 누적, function_call/call_id 검증, 순차 Tool 실행, function_call_output 전달, Tool 없는 최종 출력 분기를 구현한다. scripted Responses fake가 후속 입력의 실제 관찰·reasoning opaque 항목·여러 호출 대응을 검사하는 테스트로 검증한다.
- [x] 5.3 모델 호출 8회·Tool 시도 16회 제한과 기본 60초 Agent 실행 Budget, 소진 확인 후 추가 실행 중단, LLM HTTP I/O의 남은 Budget timeout·취소, 별도 유한 DB timeout 및 SDK 재시도 비활성화를 구현한다. fake clock/SDK와 로컬 HTTP I/O·PostgreSQL 검증으로 경계값·취소·추가 호출 중단 및 DB 대기의 Budget 초과 가능성을 확인한다. 전체 HTTP 응답의 엄격한 60초 상한은 보장하지 않는다.
- [x] 5.4 최종 JSON Schema와 Application의 결과/근거 검증을 연결한다. diagnosed/needs_information/escalate, 빈 출력·refusal/incomplete, 잘못된 참조·정책 ID·call_id 및 근거 없는 진단 테스트로 검증한다.
- [x] 5.5 메모리 Trace 수집과 공급자 model/response_id/usage 기록을 구현한다. sequence·event 필드·Tool 소요시간·null usage·오류 이벤트와 설정/원본 SDK 응답/내부 추론 비저장 테스트로 검증한다.

## 6. 실행 상태와 종료 저장

- [x] 6.1 조직/pending 조건의 원자 running claim과 시작 commit, 동기 diagnose API 및 terminal 재호출을 연결한다. 실제 두 Session의 동시 요청에서 한 실행만 호출되고 running/failed 409·completed 재조회 무호출임을 검증한다.
- [x] 6.2 Application 소유 Session에 종료 상태·Diagnosis·전체 Trace를 원자 저장하고 안전한 Agent 실패를 failed로 저장한다. 인증 autobegin 이후 실행, 200 종료 DTO·독립 연결 결과/Trace 재조회, 업무 결과와 기술 실패 구분 테스트로 검증한다.
- [x] 6.3 DB 실패 rollback과 한 번의 실패 저장 시도, commit 결과 불명확 시 terminal 상태 보호를 구현한다. 별도 PostgreSQL 연결과 오류 주입으로 부분 결과 부재·failed 보존·지속 장애의 running 잔존·원래 503 유지·completed 덮어쓰기 방지를 검증한다.
- [x] 6.4 README와 설계 요약에 동기 실행 예시, status/outcome 해석, Trace 종료 일괄 저장, 실패 재실행 및 프로세스 종료 한계를 반영한다. 실제 API 응답과 문서 예시의 일치를 통합 테스트 결과로 검토한다.

## 7. 통합 Acceptance 검증

- [x] 7.1 실제 OpenAI 호출 없이 고정 Seed/정책/기준 UTC/scripted Responses로 문의 생성→복합 진단→결과/Trace 조회 E2E를 실행한다. 역할·팀원 한도·만료·past_due/실패 결제·부재·정보 요청·이관 및 고객 snapshot 보존을 검증한다.
- [x] 7.2 전용 PostgreSQL DB에서 `uv run pytest` 전체와 `openspec validate support-agent --strict`를 실행한다. 기존 Backend 회귀 및 신규 spec 시나리오 충족을 확인하고 실행한 결과·남은 문제만 보고한다.
- [x] 7.3 구현 환경의 OPENAI_MODEL/SDK 기능·계정 접근을 확인하고 설정이 제공된 개발 환경에서 복합 문의 실제 Responses smoke를 수행한다. 후속 Tool 선택·저장 근거·종료를 확인하며 실제 실행 여부와 자동 테스트 결과를 구분해 기록한다. 설정 부재 시 수동 검증 미실행을 명시한다.

검증 기록(2026-10-22 정리):

- 기존 전체 자동화 테스트는 **185 passed, 1 warning**이다. 근거는 `study/support-agent-fixes.md`이며 이번 문서 정리에서 pytest는 재실행하지 않았다. 초기 176 passed와 실제 Smoke 미실행 기록은 이후 결과로 갱신한다.
- 사용자가 2026-10-22 실제 OpenAI Responses Smoke를 실행했다. 기존 `.smoke-results/run-m4e9wv6d/` 산출물은 HTTP 200/completed/diagnosed, LLM 6회·Tool 5회와 저장 결과/Trace 재조회를 보여 준다. 2026-10-22 Codex가 diagnosed.json·retrieved.json·report.md의 진단과 traces.json을 대조해 ST-03을 Pass로 기록했으며, ST-01~ST-05 모두 Pass로 정상 시나리오를 통과했다. 이번 정리에서 실제 API를 재호출하지 않았다.
- ST-03의 확인 사실은 member 역할(sequence 3), 인원 3/한도 10(5), 활성·미만료 pro 구독(7), succeeded/pending 결제(9) 및 검색 정책(11)이다. Evidence 7개의 sequence/정책 ID·내용과 설명이 일치한다. 초대 권한 부족은 관찰과 정책을 결합한 추론이며 실제 초대 로그에 의한 확증은 아니다. 관리자 요청 등은 후속 제안이고 실행 검증은 하지 않았다. 전체 품질·성능·Workflow 비교도 범위 밖이다. 원본 산출물 폴더는 Git 제외 대상이며 이 기록은 검토 요약이다.
- SA-02의 조회 경합은 기존 수정·회귀 검증으로 해결됐다. SA-01은 사용자 승인에 따라 엄격한 전체 deadline 대신 Agent Budget 계약으로 확정했다. 모델 I/O 취소와 별도 유한 DB timeout을 유지하며 DB 대기·동기 작업·종료 저장으로 Budget을 초과할 수 있다는 한계를 Spec/Design에 반영했다. 과거 리뷰 및 수정 기록의 판정은 당시 기록으로 보존하며 엄격한 상한이 구현됐다고 주장하지 않는다.
- 이번 정리에서 `openspec validate support-agent --strict`가 통과했고, 세 Delta를 메인 Spec으로 동기화한 뒤 `openspec validate --specs --strict`가 **5 passed, 0 failed**로 통과했다. 기존 자동화 테스트와 실제 API 호출은 반복 실행하지 않았다.
