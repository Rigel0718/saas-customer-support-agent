# Design

## Context

변경 목적은 [proposal.md](proposal.md)를 따른다. `docs/mvp.md`, `docs/sdd_workflow.md`, `AGENTS.md`와 현재 다섯 메인 Spec, 관련 구현·테스트를 확인했다. 현재 Change는 `workflow-baseline`이며 `support-agent`는 Archive되어 있다. 실제 Smoke Test 성공은 사용자 제공 배경이며 이번 작업에서 재실행하지 않았다.

현재 구조에서 재사용할 경계는 다음과 같다.

| 현재 구현 | 관찰한 책임과 재사용 방향 |
|---|---|
| `app/services/customer.py` | CallerContext 인증 범위, 네 고객 조회, not_found, 결제 정렬과 member_count 계산. Workflow도 같은 함수를 사용한다. |
| `app/services/policy.py`, `policies/*.json` | 고정 ID의 세 정책과 결정적 검색(최대 3개). 정책을 Prompt에 복사하거나 별도 규칙으로 재작성하지 않는다. |
| `app/agent/tools.py` | 기존 Service 호출·인자 검증·안전한 관찰 envelope를 이미 제공한다. Workflow의 고정 인자도 이 어댑터를 거쳐 관찰 의미를 맞출 수 있다. |
| `app/services/support.py` | pending 선점, running 확정, 최종 DiagnosisDTO/근거 검증, 종료 원자 저장, DB 실패 복구. 실행 함수 선택만 필요한 만큼 확장한다. |
| `app/agent/loop.py` | 동적 Tool 선택, Trace 조립, 취소 가능한 Responses I/O. Agent 전략은 유지하고 작은 기존 헬퍼만 재사용한다. |
| 지원 모델/DTO, API | 문의별 진단 하나, Trace sequence 고유성, 한 번 실행, 동기 응답. 복수 실행 테이블을 추가하지 않는다. |
| 기존 테스트 | Fake Responses·주입 가능한 시계, PostgreSQL 격리 fixture, 별도 세션을 이용한 durable 저장/동시성 검증을 재사용한다. |

메인 `support-diagnosis`의 고정 Workflow 대체 금지는 **Agent 실행**에 적용한다. Agent를 고정 절차로 교체하지 않고 별도 `workflow-diagnosis` capability를 추가하므로 충돌하지 않는다. 고객·정책 Spec은 변경하지 않는다.

## Goals / Non-Goals

**Goals:** 이후 Evaluation에서 Fixed-step Workflow와 동적 Tool Calling Agent의 시스템 수준 Trade-off를 비교할 첫 기준선을 제공한다. 진단 품질·근거의 타당성, 정책 준수·안전성, LLM 호출 수·Token 사용량, Tool 호출 수·불필요한 조회, 실행 지연, 실패율·실패 유형을 평가할 관찰·검증 기반을 확보한다. 권한·데이터·출력 검증·저장 계약은 공유하고, 제어 흐름은 일반 Python 함수와 명시적 예외 분기로 표현해 Fake Client로 검증한다.

**Non-Goals:** 공통 Runner 인터페이스/상속 계층, Workflow DSL/Engine, LLM 분류기·Router·Hybrid Orchestration, Case-specific Workflow, 키워드별 비즈니스 규칙 트리, 새 고객 상태 계산, 대화형 재질문, Evaluation 구현. 개발 순서는 완료된 Support Agent → 이번 Workflow Baseline → 핵심인 Evaluation Framework 및 Trade-off Analysis → 평가 결과에 따른 전용 Workflow·Hybrid Routing 검토다. Hybrid는 확정된 해법이 아니다. 이번 작업은 설계 문서 수정에 한정한다.

## Decisions

기존 D1~D5의 구현 방향을 유지하고, 비교 조건과 평가 해석의 한계를 아래와 같이 명확히 한다.

### D1. 전체 고정 조회 후 단일 LLM 진단

별도 Workflow 함수(예: `app/workflow/runner.py`)에서 다음 절차를 수행한다. 정상 경로는 고객 네 조회와 정책 세 검색으로 항상 일곱 번이다.

1. Application이 문의를 선점하고 running·실행 방식을 commit한다. 문의 생성자와 내용, 인증 조직을 신뢰된 입력으로 복사한다.
2. 실행 시작 Trace와 기준 UTC를 만들고 정책 문서를 한 번 로드한다.
3. `get_user(requester_user_id)` → `get_organization(context.organization_id)` → `get_subscription(context.organization_id)` → `get_payment_history(context.organization_id)` 순서로 실행한다.
4. `search_policy("플랜")` → `search_policy("권한")` → `search_policy("구독 결제")`를 같은 정책 집합으로 실행한다. 현재 문서에서 각각 plans, permissions, troubleshooting을 검색할 수 있고 검색 결과 전체를 보존한다. 모든 정책을 직접 주입해 검색을 우회하지 않는다.
5. 문의·대상·기준 UTC·일곱 관찰과 sequence를 단일 Responses 요청에 전달한다. Tool 정의를 제공하지 않는다. 기존 DiagnosisDTO JSON Schema와 동일한 업무 제약(읽기 전용, 사실/정책 근거, 부재·정보 요청/이관, 상태와 시각 불일치)을 안내한다.
6. Application이 기존 `validate_diagnosis`로 검증하고 `save_terminal`로 종료 상태·진단·Trace를 함께 저장한다.

명시적 분기는 다음으로 제한한다.

| 단계의 결과 | 다음 행동 |
|---|---|
| 정상 관찰, not_found, 빈 이력/검색 | 관찰을 그대로 추가하고 다음 고정 단계 수행 |
| 업무 상태(past_due, expired, failed/pending 결제 등) | 기술 오류로 바꾸지 않고 데이터로 전달 |
| 컨텍스트/정책 로딩/내부 조회 오류 | 즉시 중단, 안전한 tool_error로 종료 |
| DB 오류 | 즉시 중단, 기존 rollback·실패 저장 시도·HTTP 503 |
| Budget 소진 | 후속 작업 중단, execution_timeout |
| LLM 거부/불완전 응답/통신 실패/Tool 요청 | model_refusal/invalid_output/llm_error/invalid_output으로 종료 |
| 유효한 세 outcome | 모두 completed; 정보 부족·이관도 정상 진단 결과 |
| 최종 스키마/근거 검증 실패 | invalid_output; 수정 호출 없이 failed |

문의 유형 분류와 복잡한 분기를 추가하지 않고 이해·검증 가능한 첫 기준선을 만들기 위해 전체 고정 조회를 유지한다. Application 분기는 실행 통제와 부재 처리이며 도메인 진단 자체는 LLM이 수행한다. 단순 문의에도 과도한 조회가 생길 수 있다. 이 Fixed-step Workflow는 여러 가능한 Workflow 아키텍처 중 하나이며 전체 Workflow를 대표하지 않는다. 이후 결과는 이 Baseline과 현재 Agent 구현 사이의 비교로 해석하고 Workflow 전체나 Agent 전체의 일반적인 우열로 확대하지 않는다.

### D2. 기존 진단 API의 query 선택과 실행 방식 영속화

`POST /support/requests/{id}/diagnose?mode=workflow`를 추가하고 생략은 agent로 유지한다. content-only 생성 계약은 보존한다. 별도 Workflow endpoint는 같은 인증/상태 API를 중복시키므로 채택하지 않는다.

SupportRequest와 RequestDTO에 nullable `execution_mode`(agent/workflow)를 추가한다. pending은 null이고 기존 conditional UPDATE에서 status·started_at과 함께 선택한 방식을 저장한다. 이 선점에 성공한 실행만 데이터를 조회한다. running/failed는 방식 무관 409, completed는 같은 방식이면 기존 결과, 다른 방식이면 409다. 생략은 언제나 agent를 의미하므로 workflow 완료 문의의 생략 재호출도 409다.

방식을 Trace에만 기록하면 실행 중에는 Trace가 아직 저장되지 않아 방식을 조회할 수 없고, 완료 재호출의 방식 확인도 어려워진다. 작은 컬럼 하나와 명시적 이행 절차를 감수한다. 진단·Trace 테이블을 늘리거나 `AgentTrace`를 전면 개명하지 않는다.

### D3. Application/Service가 실행과 실패를 소유

`diagnose_request`에 기본 agent인 방식 인자를 추가하고 동일 선점·검증·종료 저장 블록 안에서 `run_agent` 또는 Workflow 함수로 분기한다. 각 실행 함수는 관찰/출력과 안전한 오류만 전달하며 직접 commit하거나 문의 상태를 갱신하지 않는다. 기존 AgentFailure를 내부 오류 전달용으로 재사용할 수 있으며 이름 정리를 위한 전면 리팩터링은 하지 않는다.

Workflow는 `execute_tool`을 고정 인자로 호출하거나 같은 Service를 직접 호출하되, 관찰 envelope와 오류 의미는 기존 어댑터와 동일하게 유지한다. 구현 시 중복이 가장 적은 기존 어댑터 재사용을 우선한다. 모델 입력이 Session/CallerContext를 만들 수 없고 본문의 사용자/조직 ID로 고정 대상을 교체할 수 없다.

`WORKFLOW_TIMEOUT_SECONDS` 기본 60을 추가하고 양의 유한 값만 허용한다. Agent 설정은 유지한다. Workflow에는 구조적으로 최대 일곱 조회와 한 LLM 호출만 있으므로 별도 최대 단계/Tool 횟수 설정은 추가하지 않는다. 취소 가능한 기존 Responses I/O 헬퍼와 주입 가능한 create/clock/now를 재사용하고, LLM timeout은 남은 Budget과 20초 중 작은 값, SDK max_retries=0으로 맞춘다. 각 조회 전후 Budget 확인과 기존 유한 DB timeout, 종료 저장의 별도 timeout을 적용한다. 전체 HTTP 응답 시간의 엄격한 상한은 약속하지 않는다.

### D4. 비교의 공통 조건과 의도한 차이

| 비교 항목 | 공통 조건 | 차이 |
|---|---|---|
| 입력/시점 | 같은 문의 내용·생성자·조직·실행자의 권한 조건, 동일 고객 데이터와 정책 버전, 비교 가능한 기준 UTC | 실행별 문의 ID·실제 실행 시각은 다름 |
| 데이터 접근 | 같은 Service·필드·정렬·정책 검색 함수와 접근 가능한 조직 | Workflow는 생성자와 자기 조직만 고정 조회, Agent는 같은 조직의 추가 사용자도 선택 가능 |
| 정책·업무 제약 | 같은 원본 정책, 환불/변경 금지, 부재와 기술 오류 구분 | Workflow 검색어는 고정, Agent는 동적 검색 |
| LLM | 같은 OPENAI_MODEL·공급자·지원되는 공통 생성 설정, 같은 출력 JSON Schema/근거 검증 | Workflow는 한 번, Agent는 관찰에 따라 반복; 입력/제어 Prompt는 각 역할에 맞음 |
| 통제 | 기본 60초 Budget, 같은 인증·DB timeout·저장·실패 계약 | Agent는 기존 모델 8회/Tool 16회 한도, Workflow는 최대 1회/7회 |
| 관찰 | 같은 결과/Trace 필드와 오류 의미 | 조회 순서·횟수·관찰 양·모델 사용량은 같게 강제하지 않음 |

같은 데이터는 동일한 **조회 가능한 원천과 권한**을 뜻한다. 실제 관찰 집합·조회 순서·모델 호출 수·Context 구성은 각 방식의 실행 차이로 보존한다. Workflow는 문의에 언급된 다른 사용자를 자동 조회하지 않으므로 Agent와 능력 차이가 있다. 이 비교는 이러한 차이를 포함한 시스템 수준 Trade-off를 대상으로 하며, 동적 Tool 선택만의 인과적 효과를 분리하는 실험이 아니다.

문의별 실행을 늘리는 대안은 이번 범위를 키우므로 같은 내용의 문의를 방식별로 생성한다. 이번에는 기존 Fake Client·고정 고객/정책 Fixture·now/clock 주입으로 위 공통 조건을 검증한다. Fake 요청에서 같은 모델과 가능한 범위의 공통 생성 설정을 확인하되, 방식별 Prompt·Context와 호출 횟수를 같게 만들지 않는다. 기준 UTC는 업무 판단의 시점을 맞추며, 소요시간 측정용 시계와 구분한다. HTTP에 평가용 reference time을 노출하지 않는다. 실제 데이터셋·반복 실행·채점·통계 처리·데이터 동결과 정책 버전 관리, 날짜 경계 방지·케이스 연결은 Evaluation Change의 책임이다.

### D5. 기존 필드의 관찰값과 별도 평가 지표를 구분한다

Workflow에서도 조회는 `tool_result`이고 tool_name/arguments/observation/error_code/duration_ms를 저장한다. 고정 조회 step은 1~7, LLM step은 8로 식별할 수 있다. 모델이 생성하지 않은 call_id는 null로 둔다. 오류 조회도 실제 시도한 기록을 남기고, 다음 조회를 실행한 것처럼 기록하지 않는다.

execution_started → 조회 Trace들 → llm_response → final_result → execution_completed 순서이며 실패 시 실행된 단계까지만 남기고 execution_failed로 끝낸다. 형식·참조 검증 실패에는 final_result의 invalid_output을 남긴다. 공급자가 제공한 model/response_id 및 input/output/total token usage만 기록하고 미제공 usage는 null이다.

| 기존 Request/Trace에서 추출 가능한 관찰값 | 해석과 별도 평가 작업 |
|---|---|
| execution_mode, status, error_code, 종료 Trace | 실행 방식·처리된 실패의 유형을 확인한다. 실패율은 평가 모집단과 분모를 정해 집계해야 하며, 지속 DB 장애·프로세스 종료로 기록이 유실될 수 있다. |
| tool_result의 개수·이름·인자·관찰 | 실제 조회 시도 수와 범위를 확인한다. 불필요한 조회인지는 케이스별 필요 정보와 대조해야 하며, 횟수 자체는 효율성이나 품질의 직접 증거가 아니다. |
| llm_response의 개수·model·response_id·usage | 실패 시도를 포함한 기록된 LLM 호출 수와 공급자 usage 기반 Token 사용량을 확인한다. usage 누락은 0으로 대체하지 않는다. 모델 비용은 실제 적용 가격 정보와 별도 계산이 필요하다. |
| Request의 finished_at - started_at | 저장된 실행 시작부터 종료 시각까지의 전체 실행 경과시간이다. HTTP 전송·실행 전 처리·종료 commit까지 포함한 전체 응답 지연을 보장하지 않는다. |
| 조회/LLM Trace의 duration_ms | 측정한 단계별 소요시간이다. 단계 합계는 준비·검증·저장 등의 시간을 모두 포함하지 않으므로 전체 실행 경과시간이나 HTTP 지연과 같지 않다. |
| diagnosis·evidence와 참조된 관찰/정책 | 의미적 진단 정확성·정책 준수·안전성·근거의 타당성은 정답·채점 기준과 별도 검토가 필요하다. Trace 존재와 구조적 참조 검증만으로 보장되지 않는다. |

이번 Change는 원천 관찰을 남기고 Fake로 기록 계약을 검증한다. Metrics DB·집계 테이블·평가 실행기를 추가하지 않으며, 실제 데이터셋·반복 실행·채점·통계 처리·가격 환산은 Evaluation Change에서 구현한다.

## Risks / Trade-offs

- 전체 조회는 단순 문제에서 불필요한 데이터·토큰·조회 실패 가능성을 늘린다 → 최소한의 이해 가능한 Baseline임을 명시하고 이후 결과 해석에서 고려한다. 평가 결과를 보고 Baseline을 몰래 변경하지 않는다.
- 단일 호출은 복합 문제에서 추가 확인이 어렵다 → needs_information/escalate를 정상 결과로 허용하고 Agent와의 의도된 차이로 유지한다.
- 두 실행이 다른 시간/데이터를 볼 수 있다 → 고정 fixture·now를 테스트에 사용하고 실제 평가의 데이터/정책/시점 고정은 별도 Change에서 검토한다.
- Workflow가 공유 지원 코드를 바꾸면서 Agent를 깨뜨릴 수 있다 → 분기 추가와 필요한 헬퍼 재사용으로 제한하고 기존 Agent Loop/권한/저장/timeout 회귀를 실행한다.
- `execution_mode` 응답 필드 추가는 엄격한 클라이언트와 테스트에 영향을 준다 → OpenAPI와 정확한 필드 집합 테스트를 함께 갱신한다.
- 기존 `create_all`은 컬럼을 추가하지 않는다 → 기존 DB 이행을 명시하고 신규 DB만으로 검증 완료를 주장하지 않는다.
- DB가 계속 실패하면 running만 남을 수 있다 → 현재 저장 계약을 유지한다. 재실행/복구 관리 기능은 이번에 추가하지 않는다.

## Migration Plan

1. 새 모델의 nullable 실행 방식 컬럼과 허용값/상태 일관성 제약을 정의한다. 신규 DB 초기화에 포함한다.
2. 기존 앱을 중지한 상태에서 기존 DB에 적용할 작은 명시적 이행 SQL과 실행 설명을 제공한다. 컬럼 추가 후 기존 pending은 null, running/completed/failed는 agent로 채우고 제약을 적용한다. 신규 migration framework는 도입하지 않는다.
3. 테스트용 기존 스키마/데이터를 대상으로 이행 전후 결과·Trace·관계 보존을 검증한다. 실제 개발 DB 데이터는 이번 계획 단계에서 변경하지 않는다.
4. 이행 후 앱을 기동하고 Fake 기반 E2E 및 Agent 회귀를 검증한다. README에 mode·한 번 실행·이행 방법만 보완한다.
5. 롤백은 실행을 중지하고 이전 앱으로 되돌리되 추가 컬럼과 기존 결과는 우선 보존한다. Workflow 결과의 방식 정보가 이전 API에서 보이지 않는 한계를 명시한다. 컬럼 삭제가 필요하면 데이터 보존을 확인한 별도 관리 작업으로 수행한다.

## Open Questions

- 향후 Evaluation에서 같은 원인에 대한 다른 문장, 정보 요청·이관을 어떤 규칙과 수동 검토로 채점할지는 미결정이다. 이번 진단/Trace 계약과 Tasks에는 영향을 주지 않는다.
- 실제 비교에서 모델 버전·공급자 조건·가격표·반복 횟수·데이터 고정 방식을 어떻게 기록할지는 Evaluation에서 정한다. 이번 Change는 원천 관찰과 주입 가능한 기준 시각까지만 제공한다.

이번 Workflow Baseline 구현 전에 추가로 합의해야 할 결정은 없다. 위 Evaluation 미결정 사항은 후속 Change에서 구체화한다.
