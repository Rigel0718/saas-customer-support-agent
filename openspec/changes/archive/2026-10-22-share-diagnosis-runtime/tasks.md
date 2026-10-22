# Tasks

구현 기준은 [design.md](design.md)와 기존 `support-diagnosis`, `support-requests`, 완료된 `workflow-baseline`의 두 delta spec이다. `skip_specs: true`이므로 새 delta spec은 작성하지 않는다. 아래 항목은 apply 단계에서 수행하며 이 계획 작성 단계에서는 코드나 테스트를 변경·실행하지 않는다.

자동 테스트는 실제 API Key 없이 Fake/Mock 및 로컬 HTTP transport를 사용한다. 환경변수 이름은 `.env.example`을 기준으로 하고 `.env` 내용은 조회·출력하지 않는다. 필요한 환경변수는 테스트 프로세스에 로딩해 개발 DB와 분리된 PostgreSQL 테스트 DB의 기존 격리 fixture를 사용한다. 실제 Smoke/외부 OpenAI 호출은 수행하지 않는다.

Prompt 공통화는 범위에서 제외한다. Agent/Workflow의 기존 최종 INSTRUCTIONS는 문구·공백·줄바꿈·문장 순서를 포함해 그대로 유지하고 중복을 허용한다. API·DB·정책·Settings·진단 Schema는 변경하지 않는다.

## 1. 중립 패키지와 실행 기반 추출

- [x] 1.1 코드 변경 전에 두 실행기의 기존 최종 INSTRUCTIONS 문자열을 독립적인 고정 기대값으로 확보하고 방식별 요청 옵션·기존 테스트 계약 및 Fake/Mock·격리 테스트 DB 구성을 확인한다. 이후 `app/diagnosis` 패키지와 `runtime.py`의 공통 ExecutionFailure·ExecutionBudget·add_trace를 구현하고 두 실행기/지원 Service 사용처를 이행한다. 고정 시계의 만료/경계·연속 sequence·UTC 기록 및 기존 Agent/Workflow Budget 테스트로 검증하며 기존 최종 INSTRUCTIONS는 모든 단계에서 유지한다.
- [x] 1.2 공통 인자 Schema·ARGUMENTS·ToolResult·execute_tool을 `diagnosis/tools.py`로 이동하고 `agent/tools.py`에는 모델용 Tool 정의만 남긴다. 테스트·Smoke 스크립트 import를 함께 이행하고 `tests/test_policy_tools.py`, `tests/test_smoke_runner.py`로 읽기 전용·인자 오류·조직 경계·내부 검증 동작을 확인한다. 이전 AgentFailure 사용처가 같은 안전한 code의 ExecutionFailure를 사용함을 확인한다.

## 2. 조회 실행과 관찰의 공통화

- [x] 2.1 `execute_observed_tool`로 한 번의 검증·DB timeout·조회·관찰/Trace·Budget 확인을 묶고 양쪽 실행기에 적용한다. 인자 재검증을 제거하며 검증된 인자만 기록한다. 기존 Tool/Loop/Workflow 테스트와 필요한 오류 주입 검사로 성공·not_found·빈 결과·unknown_tool·invalid_arguments 및 치명적 오류마다 기록 수·관찰 sequence 일치·후속 호출 중단을 확인한다.
- [x] 2.2 조회 헬퍼의 SQLAlchemyError 재전파, 안전한 오류 코드, step/call_id·duration 기록을 검증한다. `tests/test_agent_loop.py`, `tests/test_workflow_runner.py` 및 두 deadline 테스트에서 남은 Budget에 따른 DB timeout, 오류 Trace, Agent의 개별 Tool 시도 한도와 Workflow 7단계 순서/call_id=null이 유지됨을 확인한다. 이동한 함수의 monkeypatch 경로를 함께 수정한다.

## 3. 모델 호출과 관찰의 공통화

- [x] 3.1 `diagnosis/llm.py`에 취소 가능한 Responses I/O·응답 정규화·call_model·텍스트 추출을 구현하고 양쪽 실행기의 중복을 제거한다. create/clock/now 주입을 유지하고 Fake 요청 검사로 동일 모델·strict DiagnosisDTO Schema·store=False·timeout과 Agent 전용 옵션 및 Workflow Tool 정의 부재를 확인한다.
- [x] 3.2 SDK Mock을 새 모듈 경로로 이행하고 `tests/test_agent_deadline.py`, `tests/test_workflow_deadline.py` 및 관련 모델 실패 테스트를 실행한다. Budget 소진 중 I/O 취소, 선행 통신 timeout의 llm_error, 재시도 없음, refusal/미완료 분류, usage=null 및 메타데이터·duration·시도당 단일 Trace를 검증한다. 실제 공급자 통신은 사용하지 않는다.
- [x] 3.3 기존 Loop/Workflow 회귀로 Agent의 reasoning/output 이력·call_id별 관찰 전달·중복 ID 거부·후속 판단·반복·호출 한도와 Workflow의 고정 7단계 순서·수집 관찰 구성·LLM 1회·Tool 요청 거부·재조회/수정 호출 부재·빈 출력 실패를 확인한다. 코드 검토로 공통 모듈이 실행 전략이나 후속 행동을 선택하지 않음을 확인한다.

## 4. 기존 최종 Prompt 보존 검증

- [x] 4.1 apply 단계에서 Prompt 보존 검증을 추가/수정한다. 두 실행기의 최종 INSTRUCTIONS를 1.1에서 확보한 리팩터링 전 고정 기대값과 각각 완전 일치로 비교한다. 공백·줄바꿈 정규화나 변경 후 상수끼리만 비교하지 않고 문구·문장 순서 변경도 탐지한다. COMMON_INSTRUCTIONS 추출·방식별 합성·instructions.py 신설 없이 기존 두 문자열이 각 실행기에 유지됨을 확인한다.
- [x] 4.2 기존 Agent/Workflow Fake 요청 검증에 최종 instructions 완전 일치를 포함하고 공통 LLM 함수가 전달받은 문자열을 덧붙이거나 재작성하지 않음을 확인한다. 방식별 요청 옵션·입력/관찰 구성 검증도 유지하며 Prompt 검증으로 기존 실행/오류/권한 계약 검증을 대체하거나 실제 모델 품질 보존을 주장하지 않는다.

## 5. 통합 회귀와 결과 확인

- [x] 5.1 기존 지원 실행/API·Workflow E2E·공통 비교 조건 테스트로 두 방식의 선점·한 번 실행·mode 재호출·인증·인가·최종 근거 검증·원자 저장·DB 실패 503·완료 결과 보호·조직 경계와 기존 관찰 차이를 확인한다. 전용 테스트 DB 환경에서 `uv run pytest -q` 전체 회귀를 실행하고 실행/실패/미실행 결과를 기록한다.
- [x] 5.2 변경 diff와 `rg`로 Workflow의 Agent 모듈 의존 및 이전 공통 import/Mock 경로가 제거되었고 `diagnosis`가 Agent·Workflow·지원 Service를 역참조하지 않음을 확인한다. 공통 Runtime이 한 번의 호출·Observation·Trace·Budget만 담당하고 Support Service에 선점·인증·인가·최종 검증·DB 저장 책임이 유지됨을 검토한다. 기존 최종 Prompt·API·DB·정책·Settings·진단 Schema·기존 Workflow 산출물과 작업 전 사용자 변경 보존을 확인하고 구현 내용·실제 테스트 결과·평가 대상 동작 보존의 검증 한계를 한국어로 보고한다.
