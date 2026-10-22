# Proposal

## Why

Agent와 Workflow에 실행 Budget, 조회·LLM 호출의 오류 처리와 Trace 기록이 중복되어 수정 시 두 방식의 계약이 달라질 수 있다. 프로젝트의 핵심인 Trade-off Evaluation에서 평가 대상의 실제 동작을 최대한 보존하기 위해, 기존 Prompt와 실행 전략을 유지하면서 `app.agent`에 묶인 공통 실행 기반을 중립 패키지로 분리한다.

## What Changes

- `app/diagnosis`에 실행 Budget·실패 예외·Trace, 한 번의 Tool 실행과 Observation 기록, 한 번의 Responses API 호출과 오류 처리를 공통 모듈로 추출한다. 공통 모듈은 실행 전략을 선택하지 않는다.
- Agent와 Workflow의 기존 최종 INSTRUCTIONS 문자열을 각각 그대로 유지한다. 문구·공백·줄바꿈·문장 순서를 변경하지 않으며 Prompt 중복은 허용한다. 정책 내용과 출력 타입의 원본은 기존 정책 문서와 DiagnosisDTO다.
- Agent의 동적 반복·call_id 검증·호출 한도와 Workflow의 고정 일곱 조회·단일 LLM 호출은 각각의 실행기에 유지한다.
- 지원 Service는 기존 실행 선점·인증·인가·최종 검증·원자 저장 책임을 유지하고 공통 기능의 import만 중립 패키지로 변경한다. 기존 고객 Service의 조회 범위 검사도 유지한다.
- 기존 테스트·Mock 경로와 Smoke 스크립트의 내부 import를 함께 이행하고 Fake 기반 회귀로 계약 보존을 검증한다.
- 제외: COMMON_INSTRUCTIONS 추출·방식별 Prompt 합성·Prompt 문구/순서 변경, API·DB·정책·Settings·진단 Schema 변경, 진단 전략/Prompt 품질 개선, 공통 Runner 상속·Protocol·엔진, Evaluation 구현, 실제 OpenAI 호출, 기존 Change의 명세 동기화·Archive. 공통 지침 관리 방식은 필요할 때 향후 별도 Change에서 검토한다.

## Capabilities

### New Capabilities

없음. 기존 동작을 유지하는 내부 리팩터링으로 `.openspec.yaml`에 `skip_specs: true`를 선언한다.

### Modified Capabilities

없음. `support-diagnosis`, `support-requests`와 완료된 `workflow-baseline` Change의 두 delta spec을 회귀 기준으로 사용한다. 새로운 delta spec을 작성하거나 기존 요구사항을 변경하지 않는다.

## Impact

- 대상: `app/diagnosis/*`, `app/agent/loop.py`, `app/agent/tools.py`, `app/workflow/runner.py`, `app/services/support.py`, 관련 테스트 및 `scripts/support_agent_smoke.py`의 내부 import.
- HTTP 응답·오류 코드·DB 스키마·Settings·모델 설정·실행 방식별 요청 옵션·호출 수 제한·Trace 형식과 정책 데이터는 유지한다. 외부 의존성과 DB 이행은 추가하지 않는다.
- 작업 트리의 기존 Workflow 구현을 기준으로 하며 사용자 변경과 `workflow-baseline` 산출물을 수정·되돌리지 않는다. 해당 Change는 작업이 완료되었지만 아직 Archive되지 않아 Workflow 계약은 그 delta spec에서 확인한다.
- 검증 범위는 기존 Agent/Workflow 테스트 계약, Tool Observation과 Trace sequence 일치, Timeout·DB·LLM 오류 분류, 방식별 요청 옵션 및 기존 최종 Prompt 문자열 보존이다. Fake 검증은 계약 보존만 확인하며 실제 모델의 답변 동일성·품질·비용·성능을 보장하지 않는다.
