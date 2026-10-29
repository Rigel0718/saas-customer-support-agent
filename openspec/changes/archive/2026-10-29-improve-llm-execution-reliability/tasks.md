# Tasks

아래는 후속 apply에서 수행할 미완료 작업이다. 이번 propose에서는 계획 문서만 작성하며 코드·DB·Pilot 결과 변경과 실제 OpenAI 호출은 하지 않는다. 구현 기준은 [Design](design.md)과 두 [LLM 실행](specs/llm-execution/spec.md)·[Trace](specs/support-requests/spec.md) 명세다.

## 1. 공통 Timeout 설정

- [x] 1.1 `Settings` 마지막에 기본 20초의 `llm_request_timeout_seconds`를 추가하고 API/Evaluation이 공유할 작은 Timeout 파싱 함수를 구현한다. 기본값, 기존 positional 호환, 환경변수 반영, 0·음수·빈 문자열·NaN·무한대·잘못된 숫자의 안전한 거부를 실제 환경 대신 monkeypatch한 값으로 검증한다.
- [x] 1.2 `.env.example`에 `LLM_REQUEST_TIMEOUT_SECONDS=20`을 추가하고 기존 사용자 변경은 보존한다. 기존 Runbook에 요청 Timeout과 전체 Budget의 차이 및 기본 20/60초를 설명하고 변수명·기본값을 설정 테스트와 대조한다. `.env` 및 실제 Secret 파일은 조회하지 않는다.

## 2. Trace 저장 계약과 호환

- [x] 2.1 `TraceDTO`에 허용 필드만 갖는 nullable `llm_metadata`와 내부 오류 분류 모델을 추가하고 `AgentTrace`에 nullable JSONB 열을 추가한다. 메타데이터 round-trip, 추가 필드 거부, 이전 필드 누락→null, Tool observation·Diagnosis 검증 계약 불변을 단위 테스트한다.
- [x] 2.2 기존 SQL 방식으로 `migrations/002_llm_trace_metadata.sql`을 작성한다. 테스트 소유 PostgreSQL schema에서 이전 문의·진단·Trace를 만든 뒤 migration하여 내용·개수·관계 보존과 새 열 null을 검증하고 신규 ORM 초기화도 같은 구조임을 확인한다. 기존 운영/개발/Pilot schema에 migration을 실행하지 않는다.
- [x] 2.3 Runbook에 수동 migration 선행 조건과 nullable 열을 보존하는 애플리케이션 rollback 절차를 추가한다. SQL에 backfill·기존 데이터 변경·자동 시작 적용이 없고 문서가 실제 파일명을 참조함을 검토한다.

## 3. 공통 LLM 호출 관측성

- [x] 3.1 `call_model`의 상수 20을 설정값으로 교체하고 계산한 요청 Timeout을 성공·실패의 `llm_response.llm_metadata`에 기록한다. Fake `create`·고정 시계로 설정/잔여 Budget의 최소값, 시도당 Trace 한 건, 기존 `execution_timeout` 우선순위와 거부·불완전 응답 코드를 검증한다. Fake Async Client로 SDK와 요청에 같은 Timeout이 전달되고 재시도 0회임을 확인한다.
- [x] 3.2 `llm.py`에 Design D2의 고정 허용 목록 분류를 구현한다. 합성 Timeout·연결·HTTP 401/429/500·내부 ExecutionFailure·미분류 예외로 exception_type/is_timeout/http_status 및 유효 정수 Status만 보존함을 검증한다. 메시지·본문·Header·URL·사용자 정의 클래스 이름에 넣은 민감정보 표식이 Trace·오류 응답·캡처한 로그에 없음을 검증한다.
- [x] 3.3 Fake LLM을 사용하여 두 모드의 성공·처리된 실패를 저장하고 별도 세션/API Trace 조회에서 메타데이터를 확인한다. 기존 상태/오류 코드, 종료 저장 rollback·503, 조직 격리와 메타데이터가 Diagnosis 근거가 되지 않는 경계를 기존 테스트에 맞춰 검증한다.

## 4. Evaluation 설정과 결과 보존

- [x] 4.1 Evaluation `run`에 `--llm-request-timeout-seconds`, `--execution-timeout-seconds`를 추가하고 CLI→환경변수→기본값 우선순위를 `diagnosis_settings`에 연결한다. Fake CLI 테스트로 기본·환경·옵션·하위 잘못된 값 덮어쓰기·최종 모드 Budget 불일치·유효하지 않은 값 거부를 검증하고 실패 시 DB/Fixture/결과 디렉터리/LLM 호출이 0회임을 확인한다. 기존 실제 호출 허용 옵션과 Agent step/Tool 한도는 유지한다.
- [x] 4.2 manifest에 해석된 `llm_request_timeout_seconds`와 기존 두 Budget의 실제 값을 기록한다. CLI→Runner Settings→두 모드 Fake LLM 요청→Trace→결과 JSON을 연결한 테스트로 값 일치와 Budget에 따른 요청값 축소를 검증하고 전체 Settings·환경변수·키가 직렬화되지 않음을 확인한다.
- [x] 4.3 합성 이전 `evaluation-v1` 파일을 임시 디렉터리에 만들고 로드·재채점 준비 호환을 테스트한다. 누락 메타데이터·설정을 추정하지 않고 기존 usage/호출 수/지표·실패 분류가 유지되며 원본 byte/hash와 source 연결이 보존됨을 검증한다. 실제 Pilot 파일은 수정하지 않는다.
- [x] 4.4 기존 Evaluation Runbook에 두 옵션, 우선순위, 공통 Budget 불일치 처리, manifest 설정값/Trace 적용값 확인 예시, 새 결과의 Reader 호환 범위와 이전 결과 미기록 의미를 추가한다. CLI `--help`와 Fake 실행 산출물을 문서 예시와 대조하며 예시의 실제 평가 명령은 실행하지 않는다.

## 5. 통합 검증

- [x] 5.1 추가 단위·CLI·저장·migration 테스트와 기존 Agent/Workflow deadline·실행·Evaluation 회귀 테스트를 실제 외부 호출 없이 실행한다. DB 테스트는 기존 격리 검사와 테스트 소유 schema를 사용하고, 사용할 수 없는 테스트 환경은 미실행으로 명시한다. `.env`/Secret 파일 조회·출력과 실제 OpenAI 호출을 수행하지 않는다.
- [x] 5.2 diff와 테스트 결과를 대조하여 Prompt·Tool 정의/전략·Workflow 조회 순서·`needs_information` 판단·Diagnosis 검증·기존 Pilot 파일이 변경되지 않았음을 확인한다. 실행한 검증과 미실행 항목·제약을 한국어로 보고하고 Fake 통과를 실제 안정성/정확도 개선 측정으로 주장하지 않는다.

## Workflow follow-up

- 후속 실제 평가는 사용자가 별도로 명시한 경우에만 새 run으로 진행한다. 이번 Change의 Fake 검증이나 구현 요청이 실제 API 호출 승인을 뜻하지 않는다.
- 구현 검토 후 별도 요청으로 sync/archive를 진행한다. `agent-workflow-evaluation`의 미아카이브 상태와 이번 Change의 capability 소유 경계를 유지한다.
