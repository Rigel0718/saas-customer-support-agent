# Design

## Context

1차 결론(`docs/evaluation/first-evaluation-conclusion.md`)은 "필요한 정보가 작고 미리 알려져 있으면 Workflow가 유리하다"였다. 이번 Change는 그 전제가 깨지는 환경(정보량이 큰 변경 이력)을 만들고 파일럿으로 관찰하는 것까지만 다룬다. 본 평가 설계는 파일럿을 본 뒤에 결정한다.

## D1. Audit Event는 조직 범위의 읽기 전용 데이터다

- **필드:** `id`, `organization_id`(FK), `occurred_at`(UTC), `actor_user_id`(nullable, null=system), `event_type`, `target_user_id`(nullable), `payload`(JSONB)
- **`event_type`:** 고정 6종으로 제한한다. DB CHECK와 Pydantic Literal을 함께 적용한다.
- **`payload`:**
  - `role_changed`: `{from_role, to_role}`
  - `subscription_status_changed`: `{from_status, to_status, reason}`
  - 나머지 종류: 짧은 고정 키
- **사용자 FK를 두지 않는다:** 행위자와 대상은 같은 조직 사용자의 UUID로만 저장한다. 삭제된 사용자의 이력도 남아야 하기 때문이다. Fixture는 같은 조직 사용자만 참조한다.
- **migration:** `004_audit_events.sql`은 테이블 생성만 하며 backfill은 없다. 신규 초기화는 기존처럼 `Base.metadata`를 사용한다.

## D2. 조회 범위는 Application이, 필터는 LLM이 정한다

`customer.get_audit_events(session, context, *, start_at, end_at, actor_user_id, target_user_id, event_type, limit)`

- **조직 범위:** 조직은 항상 `context.organization_id`로 정한다. Tool 인자에 `organization_id`를 두지 않는다. 다른 조직 사용자의 UUID를 필터로 넣으면 빈 결과가 나온다. 존재 여부도 노출하지 않는다.
- **정렬:** `occurred_at DESC, id ASC`로 고정한다.
- **반환값:** `items[:limit]`, 필터에 맞는 전체 건수 `total_count`, `truncated = total_count > limit`.
  - `total_count`를 주는 이유는 Agent가 "결과가 잘렸으니 범위를 좁혀야 한다"를 알 수 있게 하기 위해서다. 정보를 가리지 않는 것이 공정하다.
- **인자 검증:** 
  - `limit`은 1~50이어야 한다(Agent Tool 기준).
  - `start_at > end_at`은 invalid_arguments다.
  - 모든 필드는 strict Tool schema를 위해 nullable 필수 인자로 둔다.
- **Workflow의 한도:** Workflow는 같은 Service를 `limit=500`으로 호출한다. 500은 Tool 인자 검증을 거치지 않는 Application 상수다.

## D3. Workflow는 "전부 가져오기"를 공정한 상한으로 구현한다

- **추가 조회:** 기존 7단계 뒤에 두 단계를 추가한다.
  - `get_audit_events(start_at = 기준 시각 - 14일, end_at = 기준 시각, limit = 500)`
  - `search_policy("접근")`
- **14일과 500건을 고른 근거:**
  - 운영자가 미리 정할 만한 합리적인 기본값이다.
  - 500건은 이벤트당 약 60~80 tokens로 약 35k input tokens 수준이다. 비용과 컨텍스트 상한을 고려한 값이다.
- **잘림 기록:** Workflow 관찰에도 `truncated`와 `total_count`가 그대로 포함된다. 파일럿에서 잘림이 정답에 영향을 줬는지 확인할 수 있다.
- **이 설계가 만드는 Trade-off 자체가 관찰 대상이다.** 상한을 키우면 Token이 늘고, 줄이면 누락 위험이 생긴다. 상한값은 Golden Set을 보고 튜닝하지 않는다.
- **기존 invite Case에 미치는 영향:** 이벤트가 없으므로 빈 관찰이 하나 추가된다. invite Workflow의 Prompt는 변경하지 않는다.
  - 예외: Audit Event 해석 원칙은 `workspace_access` 정책 문서로 전달한다.

## D4. Agent는 Tool 하나만 추가하고 그대로 둔다

- **변경하는 것:** `ARGUMENTS`에 `get_audit_events`를 추가한다. Tool 설명은 "조직 변경 이력 조회, 최신순, 최대 50건, total_count로 잘림 확인"이다.
- **변경하지 않는 것:** Agent Prompt, 실행 한도(8 steps / 16 Tool calls), 모델
- **이유:** Agent를 개선하면 비교 대상 자체가 바뀌기 때문이다.

## D5. 파일럿은 기존 Evaluation과 분리된 얇은 스크립트다

- **분리하는 이유:** 기존 `evaluation/dataset.py`는 invite 전용 Literal로 고정되어 있다. 이를 일반화하는 것은 이번 범위를 넘는다.
- **재사용하는 것:**
  - `validate_database_urls`, `verify_database`, `create_run_schema`(격리 schema)
  - `diagnose_request`(기존 Support 계약)
  - Trace의 usage 집계 방식
- **Case(`evaluation/access_pilot.py`의 상수):**

  | Case | 상황 | 기대 진단 |
  |---|---|---|
  | A01 | 요청자가 1일 전 관리자 B에 의해 admin→member로 강등됨. 구독은 활성 | 역할 강등(B, 시점) |
  | A02 | 1일 전 결제 실패 후 system이 구독을 active→past_due로 변경. 요청자는 admin | 구독 정지 |
  | A03 | A01과 A02의 원인이 동시에 있음 | 두 원인 모두 |
  | A04 | 관련 변경 없음. 요청자의 5일 전 member→admin 승격 이벤트만 있음(함정, Workflow 14일 범위 안) | needs_information |

  - 모든 Case의 문의 내용은 같다: "어제까지 되던 멤버 관리 기능을 오늘부터 사용할 수 없습니다. 무엇이 바뀌었는지 확인해 주세요."
- **노이즈(`seed`, `N`):**
  - 같은 조직에 추가 member 20명을 둔다.
  - 기준 시각 이전 30일에 걸쳐 N건을 생성한다.
  - 종류는 `user_login`, `document_*`, `member_invited`, 그리고 **요청자가 아닌 사용자의 `role_changed`(혼동 유발)**다. 혼동용 이벤트가 있어야 종류 필터 하나로 원인을 바로 찾을 수 없다.
  - 같은 `(seed, N, case)`는 같은 이벤트를 만든다. ID는 uuid5, 시각은 기준 시각 기준 오프셋으로 정한다.
- **실행 스크립트(`scripts/access_pilot.py`):**
  - 인자: `--noise 50,5000 --cases A01..A04 --repetitions 1 --seed 7`
  - 매 실행마다 새 문의를 만든다. 모드 순서는 반복마다 교대한다.
  - 결과는 invite 평가와 같은 버전 규칙으로 `.evaluation-results/access-<실행 수>-v<버전>/`에 저장한다. `manifest.json`(실행 조건·plan·출처), `executions.json`(실행별 기록), `summary.json`(모드·N·Case별 집계)으로 나누며, 채점이 없으므로 `judgments.json`은 없다. 각 실행의 기록 항목:
    - case, N, mode, status, outcome, summary, causes
    - LLM/Tool calls, input/output tokens, latency
    - Workflow audit 관찰의 `truncated`/`total_count`
    - 전체 Trace
- **채점하지 않는다.** 판정은 사람이 Trace를 직접 읽고 한다. 결과를 바탕으로 본 평가의 GT와 Judge 범위를 정한다.

## D6. 보안·권한

- **새 Tool은 읽기 전용이다.** Audit Event를 변경하는 경로가 없다.
- **조직 범위는 LLM 인자로 바꿀 수 없다.** D2를 따른다. 다른 조직의 이벤트는 어떤 필터로도 반환되지 않는다.
- **파일럿 스크립트의 DB 접근:** 기존 Evaluation과 같은 DB URL 검증을 통과해야 한다. 개발·테스트 DB와 다른 평가 DB의 새 schema에만 쓴다.

## D7. 새 정책 문서는 기존 검색 결과를 바꾸지 않는다

- 정책 검색은 토큰 포함 점수와 `document_id` 오름차순 동점 정렬로 최대 3건을 반환한다. 새 문서가 기존 검색어에서 동점으로 앞에 서면 invite Case의 관찰이 바뀐다.
- 그래서 문서 ID를 `workspace_access`로 정해 동점일 때 뒤로 가게 하고, 본문에 `플랜`·`권한`·`구독`·`결제`·`초대` 토큰을 쓰지 않는다(구독 상태는 `subscription status`로 표기한다).
- Workflow의 기존 세 검색어(`플랜`, `권한`, `구독 결제`)와 `초대` 검색 결과는 변경 전과 같다. Agent가 다른 검색어(예: `past_due`, `역할`)를 쓰면 새 문서가 포함될 수 있으며, 이는 두 모드에 공통인 새 정보다.

## Risks / Open Questions

- **Workflow 상한:** 500건과 14일은 임의로 정한 기본값이다. 파일럿에서 잘림이 원인 누락으로 이어지면, 그것이 Upfront Retrieval의 실제 한계인지 상한 선택의 문제인지 분리해 해석해야 한다.
- **현재 상태만으로 풀리는 Case:** A01과 A02는 현재 상태(role, subscription)만으로도 원인을 추정할 수 있다. Audit Log가 필수 정보가 되는 정도는 "무엇이 바뀌었는지(누가, 언제)"라는 문의 요구에 달려 있다. 파일럿 결과를 보고 Case 설계를 다시 검토한다.
- **인과 체인 깊이:** 깊이를 변수로 두는 실험은 이번 범위에서 제외한다.
