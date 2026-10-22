# Design

## Context

동기는 [proposal.md](proposal.md)를 따른다. 현재 `app/agent/loop.py`는 INSTRUCTIONS, Trace 추가, Responses I/O, Budget, 모델·조회 실행과 동적 제어를 함께 소유한다. `app/workflow/runner.py`는 앞의 세 헬퍼를 Agent에서 가져오면서 Budget·모델/조회 처리 대부분을 다시 구현한다. `app/agent/tools.py`는 공통 조회 어댑터와 Agent용 Tool 정의를 함께 담고 있다.

`app/services/support.py`는 이미 두 방식에 공통인 실행 선점·방식 저장·최종 스키마/근거 검증·원자 저장·DB 실패 복구를 소유한다. 이 경계는 유지한다. 현재 작업 트리에 있는 Workflow 구현을 기준으로 하며 관련 Change의 설계가 배제한 전면 리팩터링은 이 별도 Change에서 수행한다. 기존 main spec과 Workflow delta의 요구사항 자체는 변경하지 않으므로 specs는 생략한다.

## Goals / Non-Goals

**Goals:** 한 번의 Tool 실행·LLM 호출·Observation 기록·Budget 처리를 공통으로 관리하고 평가 대상의 실제 동작을 최대한 보존한다. 기존 최종 INSTRUCTIONS 문자열과 방식별 요청 옵션을 유지하며 두 실행기를 읽으면 다음 행동을 누가 선택하는지 직접 파악할 수 있어야 한다.

**Non-Goals:** COMMON_INSTRUCTIONS 추출·방식별 Prompt 합성·기존 Prompt 문구/순서 변경, 실행기 상속·Protocol·등록소, mode 분기를 포함한 통합 Runner, 모든 의존성을 담는 Runtime 객체, 정책 규칙 복제, API·DB·정책·Settings 변경. 문의 입력 JSON 조립처럼 작고 방식에 가까운 코드는 무조건 공통화하지 않는다.

## Decisions

### D1. 중립적인 세 모듈과 단방향 의존성

```text
app/diagnosis/
  __init__.py
  runtime.py       # ExecutionFailure, ExecutionBudget, add_trace
  tools.py         # 인자 Schema/ARGUMENTS, ToolResult, 조회 실행과 관찰
  llm.py           # Responses I/O, 공통 모델 호출/검사, 텍스트 추출

services/support --> agent/loop ------> diagnosis --> customer/policy services
                 --> workflow/runner -> diagnosis
                 --> diagnosis/runtime
agent/tools --------------------------> diagnosis/tools
```

`diagnosis`는 Agent·Workflow·지원 Service를 import하지 않는다. `app/agent/tools.py`에는 모델에 노출하는 `TOOL_DEFINITIONS`와 그 설명만 남기며 인자 Schema는 공통 ARGUMENTS에서 얻는다. 실제 Service의 인증·조직 범위·데이터 필드·정렬 계약을 재사용한다.

대안인 Agent 모듈에서 계속 재사용하는 방식은 기능 소유권을 설명하기 어렵고, 공통 기반 Runner는 서로 다른 제어 흐름을 숨긴다. 현재 두 함수와 작은 헬퍼로 충분하다.

### D2. 기존 최종 INSTRUCTIONS 문자열 보존

Agent의 `app/agent/loop.py`와 Workflow의 `app/workflow/runner.py`에 있는 기존 INSTRUCTIONS를 각각 그대로 유지한다. 이 Change에서는 `diagnosis/instructions.py`를 만들거나 COMMON_INSTRUCTIONS를 추출하지 않으며 방식별 Prompt 합성을 수행하지 않는다. 중복된 지침은 현재 단계에서 허용한다.

보존 기준은 의미의 유사성이 아니라 모델에 전달되는 최종 문자열의 완전 일치다. 문구·공백·줄바꿈·문장 순서를 변경하거나 공통 모듈에서 접두/접미 지침을 덧붙이지 않는다. 두 실행기는 자신의 기존 INSTRUCTIONS를 공통 LLM 호출 함수에 명시적으로 전달하고 공통 함수는 그 값을 그대로 Responses 요청에 사용한다.

구현 시작 전에 리팩터링 전 두 문자열을 독립적인 고정 기대값으로 확보한다. apply 단계에서 Fake가 받은 instructions와 각 실행기의 최종 문자열을 이 기대값과 대조한다. 변경 후 상수끼리만 비교하거나 공백을 정규화한 비교로 보존을 판정하지 않는다. 현재 계획 수정 단계에서는 코드와 테스트를 변경하지 않는다.

기존 DiagnosisDTO JSON Schema, 정책 문서, Application의 권한 검사와 결과 검증도 유지한다. 실제 정책이나 새로운 업무 지침을 공통 Runtime에 추가하지 않는다.

공통 Prompt 합성은 문구와 순서 변경으로 평가 조건에 영향을 줄 수 있으므로 이번 범위에서 제외한다. 공통 지침 관리 방식은 필요할 때 향후 별도 Change에서 검토한다.

### D3. 실행 Budget과 Trace의 공통 기반

`runtime.py`의 작은 `ExecutionBudget`은 monotonic deadline과 주입된 clock을 보관하고 `remaining()`에서 양의 남은 시간 또는 `ExecutionFailure("execution_timeout")`를 반환한다. Agent/Workflow의 설정 선택은 실행기에 남긴다. wall-clock `now`는 Trace와 업무 기준 UTC에 사용하며 Budget 시계와 구분한다.

기존 `add_trace`를 이동해 전체 실행의 연속 sequence와 created_at을 관리한다. 시작 이벤트는 실행기가, 최종 검증/종료 이벤트는 지원 Service가 계속 결정한다. 정책 로딩은 실행당 한 번이며 기존 tool_error 분류와 Budget 확인을 보존한다. 상태·DB commit은 이 모듈이 수행하지 않는다.

공통 예외 이름은 `ExecutionFailure`로 변경하되 code 값과 API 표현은 유지한다. 저장 모델 `AgentTrace`는 개명하지 않는다.

### D4. 관찰을 포함하는 한 번의 조회 실행

`diagnosis/tools.py`는 기존 `execute_tool`의 인자 검증·Service 호출·안전한 관찰 envelope를 이동하고, 두 실행기가 사용하는 `execute_observed_tool`을 제공한다. 직접 어댑터 계약을 검사하는 기존 테스트도 유지한다. 인자 검증과 실제 조회를 내부 단계로 나누어 Trace를 위한 재검증을 제거하고, raw 모델 인자는 기록하지 않는다.

조회 헬퍼는 다음 한 번의 실행만 담당한다.

1. Budget 확인 및 검증된 인자 확보.
2. 기존 남은 Budget과 5초를 사용한 statement/lock timeout 설정.
3. 다음 Trace sequence를 관찰에 부여하고 기존 Service/정책 검색 호출.
4. 성공·부재·잘못된 인자·기술 실패의 Trace를 한 번 기록하고 경과시간 저장.
5. 정상 반환 후 Budget을 확인하고 ToolResult 반환.

호출자는 Tool 이름·raw JSON 인자·step·nullable call_id를 제공한다. sequence 예약과 Trace 추가 사이에 다른 이벤트를 넣지 않아 관찰의 trace_sequence와 Trace sequence를 일치시킨다. 잘못된 인자·unknown_tool은 기존 안전한 오류 관찰로 반환하며 고객/정책 조회 Service를 실행하지 않는다. not_found와 빈 결과는 계속 가능한 관찰로 유지한다.

치명적 내부 오류는 안전한 tool_error로 중단한다. SQLAlchemyError는 database_error Trace를 남긴 후 그대로 전파해 기존 rollback·503·실패 저장 경로를 보존한다. 헬퍼는 commit/rollback하지 않는다. Tool 시도 횟수와 한도는 Agent가 계속 관리한다.

대안인 각 실행기의 조회 try/except 유지는 Trace와 오류 계약의 중복을 남긴다. Service에 Trace 책임을 넣으면 일반 고객 조회 API까지 실행기 관찰에 결합되므로 공통 어댑터에서 처리한다.

### D5. 관찰을 포함하는 한 번의 모델 호출

`llm.py`는 `responses_create`, `as_dict`와 중복된 호출 처리를 이동·통합한다. `call_model`은 Settings, instructions, input, Budget, Trace/step, create·now 주입을 받아 공통 설정으로 한 번 호출하고 정규화한 응답을 반환한다.

- 동일 모델·store=False·DiagnosisDTO의 strict JSON Schema를 사용하며 실행기가 전달한 기존 instructions 문자열을 변경 없이 요청에 넣는다.
- HTTP timeout은 남은 Budget과 20초 중 작은 값이며 실제 SDK 경로는 남은 Budget으로 I/O를 취소하고 max_retries=0을 유지한다.
- LLM 시도마다 기존 메타데이터·안전한 usage·duration 또는 오류 Trace를 한 번 남긴다. usage 미제공은 null이며 암호화된 reasoning을 Trace에 저장하지 않는다.
- Budget 소진/통신 실패/거부/미완료의 기존 오류 분류와 검사 순서를 보존한다.
- Agent가 명시적으로 Tool 정의·parallel_tool_calls=False·reasoning.encrypted_content 포함 옵션을 전달한다. Workflow 요청에는 이 Agent 전용 옵션을 추가하지 않는다. 공통 함수가 mode로 실행 방식을 추론하지 않는다.

function_call의 허용·실행 여부는 여기서 결정하지 않는다. 응답의 원래 output 항목을 보존해 Agent가 reasoning 등 필요한 이력을 다음 요청에 전달할 수 있게 한다. 공통 텍스트 추출 함수는 출력 텍스트만 추출하며 최종 DiagnosisDTO/근거 검증은 지원 Service가 수행한다. Workflow의 Tool 요청 거부와 빈 텍스트 검사는 실행기에 남겨 기존 분기 위치를 보존한다.

대안인 최종 진단 DTO까지 반환하는 LLM 래퍼는 Agent의 중간 Tool 요청을 처리하기 어렵고, 지원 Service의 최종 검증 책임을 중복시킨다.

### D6. 실행기의 알고리즘과 관찰 계약 보존

공통 Runtime은 호출자가 지정한 한 번의 Tool 실행 또는 LLM 호출과 그 Observation·Trace·Budget만 처리한다. 실행 방식 선택, 다음 행동 판단, 반복·재조회·재시도, 고정 단계 구성, 문의 상태 전이·인증·인가 정책·최종 검증·DB 저장을 소유하지 않는다. 기존 인증된 CallerContext 주입과 고객 Service의 접근 범위 검사는 유지한다.

Agent에는 모델 반복, call_id의 유효성/중복 검사, Tool 시도 한도, 반환 순서대로 조회, 원래 모델 output과 call_id별 관찰의 후속 전달을 남긴다. Tool 요청과 텍스트가 같이 오면 기존처럼 Tool 처리로 진행한다.

Workflow에는 생성자 사용자·조직·구독·결제·플랜·권한·구독 결제 순서, 고정 인자, 관찰 집합 구성, 단일 모델 호출과 function_call 거부를 남긴다. 정상 조회 step=1~7, LLM step=8, call_id=null을 보존한다. Agent의 step은 모델 turn을 의미하므로 Workflow와 동일하게 재정의하지 않는다.

`services/support.py`는 공통 예외/Trace의 import를 변경하고 runner 선택·선점·인증·인가·최종 검증·원자 저장을 유지한다. 기존 runner의 create/clock/now 주입 경계를 유지해 Fake와 고정 시계 테스트를 재사용한다. 내부 모듈 경로 변경에 맞춰 테스트의 import/monkeypatch와 Smoke 스크립트를 함께 이행하되 기존 테스트 계약은 유지한다. 저장소 내부 사용처를 모두 바꾸며 호환용 재export 계층은 추가하지 않는다.

## Risks / Trade-offs

- 공통화 중 DB 예외를 안전한 일반 실패로 바꾸면 503·rollback 계약이 깨진다 → DB 예외는 Trace 기록 후 재전파하고 durable 저장 회귀를 실행한다.
- 호출 전후 Budget 검사·Trace 시점 변경으로 추가 작업이나 기록 누락이 생길 수 있다 → 고정 시계·로컬 Mock HTTP I/O 취소·오류 주입 테스트로 호출 중단과 실제 기록 수를 확인한다. 전체 HTTP의 엄격한 시간 상한을 새로 약속하지 않는다.
- 중복 Trace 또는 관찰 참조 불일치 → 한 번의 호출과 그 Trace를 동일 헬퍼가 소유하고 기존 evidence 검증/E2E를 유지한다.
- 공통 호출 래퍼가 Prompt나 방식별 요청 옵션을 바꾸면 평가 조건이 달라질 수 있다 → 리팩터링 전 고정 기대값과 Fake 요청으로 최종 instructions 완전 일치와 기존 옵션/입력 구성을 검증한다. Prompt와 옵션을 유지해도 Fake 통과로 실제 답변 동일성이나 품질을 주장하지 않는다.
- 공통 모듈이 다음 행동·반복·저장을 흡수하면 실행 방식의 차이가 바뀔 수 있다 → D6의 책임 경계와 기존 Agent/Workflow 테스트 계약을 함께 검토한다.
- 내부 import 이동으로 SDK Mock이 실제 통신을 막지 못할 수 있다 → 새 `diagnosis.llm` 경로에서 SDK를 대체하고 로컬/Mock transport 외 통신을 하지 않는 기존 테스트 의도를 유지한다.

## Migration Plan

1. 구현 시작 전에 리팩터링 전 Agent/Workflow의 최종 INSTRUCTIONS와 방식별 요청 옵션·기존 테스트 계약을 확인하고 문자열의 독립적인 고정 기대값을 확보한다. 전용 테스트 DB와 Fake/Mock 경로를 확인한다. 이번 계획 수정에서는 구현과 테스트를 변경하지 않는다.
2. 기존 INSTRUCTIONS를 두 실행기에 그대로 둔 채 공통 기반·조회·LLM을 옮기고 실행기·지원 Service·스크립트·테스트 import를 함께 이행한다. 각 단계에서 관련 회귀를 실행하고 공통 모듈이 D6의 실행기/Service 책임을 흡수하지 않았는지 확인한다. Prompt 공통화 단계는 없다.
3. 독립적인 기대값과 Fake 요청으로 기존 최종 문자열의 완전 일치 및 방식별 요청 옵션을 검증한다. Tool Observation과 Trace sequence 일치, Timeout·DB·LLM 오류 분류, Agent의 동적 반복/호출 한도와 Workflow의 고정 7단계/LLM 1회를 확인한다.
4. `.env`를 직접 읽거나 출력하지 않고 필요한 환경변수를 테스트 프로세스에 로딩한다. 실제 OpenAI API Key·외부 통신 없이 전용 PostgreSQL 테스트 DB에서 전체 테스트를 실행한다. 자동 검증으로 Smoke 스크립트의 내부 검증 함수는 확인하되 실제 Smoke 실행은 하지 않는다.
5. API·DB·정책·Settings·진단 Schema 변경 없음과 공통 패키지의 의존 방향을 검토한 뒤 결과를 보고한다. 기존 Workflow Change는 별도로 유지한다.
6. DB 이행은 없다. 롤백이 필요하면 이 Change의 코드·import 변경을 함께 되돌리며 기존 Prompt와 작업 전 사용자 변경은 보존한다.
