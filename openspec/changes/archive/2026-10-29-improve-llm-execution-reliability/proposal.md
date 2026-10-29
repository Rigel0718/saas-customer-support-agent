# Proposal

## Why

Pilot Evaluation v1의 C04·C05 Workflow는 약 20초 후 `llm_error`로 끝났지만, 원본 예외 분류가 없어 Timeout 여부를 확정할 수 없다. 공통 LLM 호출의 안전한 관측 정보와 실제 적용되는 Timeout 설정을 제공하여 진단 전략을 유지한 채 후속 실행 실패를 분석할 수 있게 한다.

## What Changes

- 공통 LLM 호출 Trace에 허용된 예외 타입, 감지된 Timeout 여부, HTTP Status 및 요청별 실제 적용 Timeout을 기록한다. 예외 메시지·스택·요청/응답 본문·Header·API Key는 수집하지 않는다.
- 개별 요청 Timeout을 `LLM_REQUEST_TIMEOUT_SECONDS`로 설정한다. 기본 20초와 전체 Agent/Workflow Budget 기본 60초, SDK 재시도 0회 및 기존 오류 코드 매핑은 유지한다.
- Evaluation `run`에 요청 Timeout 및 공통 실행 Budget 옵션을 추가한다. 명시 옵션, 환경변수, 기본값 순서로 해석하며 두 모드의 동등한 시간 설정을 검증한다.
- 새 Evaluation manifest에는 해석된 설정, 각 LLM Trace에는 Budget으로 줄어든 실제 요청 Timeout을 남긴다. 기존 결과는 읽을 수 있어야 하며 누락 정보를 추정하여 보충하지 않는다.
- Fake Client·고정 시계 기반 회귀 테스트와 기존 Trace 저장/조회 및 JSON 호환 검증을 추가한다.

이번 단계는 한국어 계획 문서 작성까지만 수행한다. Agent Prompt, Tool 선택·검색 전략, Workflow 조회 순서, `needs_information` 판단, Diagnosis 검증, Judge, Metrics 정의, 재시도 정책은 변경하지 않는다. 기존 Pilot 결과 수정·재실행, `.env`/Secret 파일 조회 및 실제 OpenAI 호출은 수행하지 않는다.

## Capabilities

### New Capabilities

- `llm-execution`: 두 진단 방식이 공유하는 요청 Timeout 설정·Budget 연동, 안전한 LLM 오류 분류와 Evaluation 설정 반영·보존 계약.

### Modified Capabilities

- `support-requests`: 기존 Trace에 nullable LLM 진단 메타데이터를 추가하고 종료 저장·조회 및 이전 기록 호환을 보장한다. Diagnosis 계약은 변경하지 않는다.

## Impact

- 예정 변경: `app/config.py`, `app/diagnosis/llm.py`, `app/schemas/support.py`, `app/models/support.py`, `scripts/evaluate.py`, `evaluation/results.py`, 관련 테스트와 기존 Runbook·`.env.example`.
- `agent_traces`에 nullable JSONB 열 하나를 추가하는 작은 SQL migration이 필요하다. 기존 SQL migration 방식으로 수동 적용하며 자동 migration 프레임워크나 새 필수 의존성은 도입하지 않는다.
- Trace API는 필드 추가이며 기존 오류 코드·문의 상태·조직 경계·원자적 저장은 유지한다. 기존 DB는 새 코드 실행 전 migration이 필요하다.
- `agent-workflow-evaluation`은 구현 작업 29개가 완료됐지만 아직 archive/sync되지 않았다. 그 구현과 결과 계약을 재사용하며 기존 Change 문서는 수정하지 않는다. 이번 Change는 별도 `llm-execution` capability와 `support-requests` delta만 소유하고, 기존 평가 명세의 Runner/Prompt 불변 원칙을 유지한다.
