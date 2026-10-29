# Proposal

## Why

현재 Sequential Agent와 Fixed-step Workflow는 공통 데이터·진단·Trace 계약을 갖추었지만, 동일 조건의 반복 실행과 의미 채점·집계를 수행하는 Evaluation이 없다. 팀원 초대 실패 5개 Case로 품질·효율·실행 안정성의 Trade-off를 측정하여, 특정 구조의 우수성을 전제하지 않고 현재 두 구현이 적합한 조건의 근거를 확보한다.

## What Changes

- 동일 문의를 사용하는 C01~C05 JSON Dataset과 분리된 Ground Truth(GT)를 정의한다. 기존 Customer Service와 로컬 정책을 기준으로 단일 원인, 정보 부족, 세 조건의 복합 위반을 구분한다.
- 별도 Evaluation PostgreSQL DB와 실행별 전용 schema에 Customer Fixture를 구성하고 기준 UTC·User 수·구독 상태·정책 버전을 검증한다. 개발 Seed를 변경하거나 기존 데이터를 초기화하지 않는다.
- 기존 Support Service를 직접 재사용하는 순차 Runner를 추가한다. 매 실행마다 새 문의를 만들고 반복별 모드 순서를 교차하여 10회/50회 실행을 지원한다.
- 기존 Trace로 호출 수·Token Usage·Estimated Cost를 계산하고 Runner 경계에서 End-to-end Latency를 측정한다. Execution Success와 Diagnosis Correctness를 분리한다.
- GT와 `summary + causes`를 비교하는 모드 비공개 LLM Judge, Python의 outcome 검사, 사람의 Calibration Set 검증을 추가한다. 불명확한 의미와 Judge 기술 실패를 구분하여 판정 보류한다.
- 원본 Diagnosis·실행·Trace와 재채점에 필요한 버전을 `manifest.json`, `executions.json`, `judgments.json`, `summary.json`에 보존한다. 실패·누락 데이터·분모와 성공 실행/전체 실행 통계를 명시한다.
- 구현 단계에서 Fake/Mock 기반 자동 테스트와 한국어 `docs/evaluation_runbook.md`를 작성한다. 실제 실행과 Judge 호출은 사용자의 명시적 실행으로만 허용한다.

초기 제안은 계획 문서 작성 범위였으며 기존 구현 완료 기록은 Tasks 1~8에 보존한다. 승인된 Calibration v2 후속 작업은 대표성 조건과 검증 범위 표시, 입력 데이터 분리, Fake 회귀 테스트 및 Runbook 보정에 한정한다. 실제 OpenAI 호출·Calibration 실행과 기존 평가 결과 수정은 하지 않는다. Hybrid Router, Parallel Tool Calling, Prompt 최적화, Workflow Engine/DSL, 범용 평가 프레임워크, 외부 플랫폼, 새 비즈니스 규칙, Diagnosis 구조 변경 및 Groundedness/Task Success/Safety/Tool Selection Quality 독립 지표는 제외한다.

Calibration v2는 correct/incorrect의 확정 Human Label을 필수로 하고 review_required는 선택적으로 비교한다. ambiguous 시나리오를 강제하지 않으며 기존 대표 Case·나머지 시나리오·핵심 오답·일치율·기술 실패 조건은 유지한다. 기준 버전과 Label별 기준 사례 수·미검증 유형을 결과에 남기고, 미검증 유형을 포함한 전체 Judge 품질을 보장하지 않는다. Judge 판정 계약과 Rubric은 변경하지 않는다.

## Capabilities

### New Capabilities

- `diagnosis-evaluation`: 초대 실패 Dataset·Fixture·GT의 재현성, 두 모드의 공정한 반복 실행, 독립 채점·Calibration, Metrics·실패 집계 및 재채점 가능한 JSON 결과 계약.

### Modified Capabilities

없음. `customer-data`, `customer-read-access`, `policy-search`, `support-requests`, `support-diagnosis`, `workflow-diagnosis`의 요구사항과 공개 API를 재사용한다.

## Impact

- 구현 예정 영역: `evaluation/`의 JSON·Python 모듈, 얇은 `scripts/evaluate.py` CLI, `tests/test_evaluation_*.py`, `.env.example`의 변수명 안내와 `docs/evaluation_runbook.md`.
- 재사용 경계: `app.services.customer.authenticate`, 고객 조회 Service, `app.services.support`의 생성·실행·조회·검증, `app.db.build_engine`, 공통 ORM과 Diagnosis Runtime. Agent Loop/Workflow/정책 검색 알고리즘은 변경하지 않는다.
- 별도 DB는 운영자가 미리 준비하고 Runner는 검증된 실행 전용 schema에 기존 일곱 테이블만 생성한다. 기존 DB migration, Metrics DB/테이블, 새 HTTP Endpoint, 새 필수 라이브러리는 추가하지 않는다.
- `docs/mvp.md`의 30개 Case 및 더 넓은 지표는 장기 방향이다. 이번 Change의 완료 범위는 초대 실패 5개 Case와 요청된 일곱 Metrics로 제한한다. 현재 코드 계약·문서 차이와 측정 한계는 Design에 기록한다.
