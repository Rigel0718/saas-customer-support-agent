# Workflow Baseline Smoke Test

사용자가 개발 환경에서 실행하는 실제 OpenAI API Smoke Test다. 진단 요청은 한 번이며 비용이 발생할 수 있다. Codex의 검증 범위는 구현·문서·Fake 테스트까지이고 실제 Smoke Test는 사용자가 직접 수행한다.

## 1. 최초 1회 준비

저장소 루트에서 `uv`와 Docker Compose를 준비하고 [.env.example](../.env.example)을 기준으로 로컬 `.env`를 설정한다. Secret은 문서·터미널 출력·Git에 남기지 않는다.

| 설정 | 준비 내용 |
|---|---|
| `POSTGRES_PASSWORD`, `POSTGRES_PORT`, `DATABASE_URL` | 개발 PostgreSQL과 일치하는 값. 기존 volume 비밀번호는 `.env` 변경만으로 바뀌지 않는다. |
| `DEV_BEARER_TOKENS` | 기존 Token → 사용자 UUID 매핑을 사용한다. owner 매핑만 있어도 된다. 실행기가 임시 Backend에 member용 토큰을 추가하며 `.env`는 수정하지 않는다. |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | 실제 사용 가능한 Key와 Responses API JSON Schema 지원 모델. 실제 진단은 Backend의 설정을 사용한다. |
| `WORKFLOW_TIMEOUT_SECONDS` | 양의 유한 값, 기본 60초. 임시 Backend에도 같은 값을 적용한다. |

실행기는 고정 Seed member를 대상으로 임시 토큰을 생성한다. Token이나 DB Password를 매 실행마다 입력할 필요는 없다.

**개발용 DB에서만 실행한다.** 실행기가 PostgreSQL을 시작하고 없는 테이블을 생성하며 Seed를 현재 UTC 기준으로 갱신한다. 기존 Seed 수정값은 덮어쓸 수 있다. 기존 문의·진단·Trace는 삭제하지 않는다. `TEST_DATABASE_URL`은 사용하지 않는다.

기존 `support_requests`에 `execution_mode` 컬럼이 없으면 [001 migration](../migrations/001_workflow_execution_mode.sql)을 트랜잭션 안에서 자동 적용한다. 기존 pending은 null, 나머지 상태는 agent로 기록한다. 이미 컬럼이 있으면 migration을 생략한다. 이 자동 이행은 현재 제공된 001에만 해당하며 범용 migration 실행기가 아니다.

## 2. 매번 실행 전 확인

Docker가 실행 중이어야 한다. 기존 DB를 이행하는 경우 해당 DB를 사용하는 다른 Backend와 진단을 중지하고 중요한 개발 데이터를 백업해 둔다. 별도로 PostgreSQL·Backend를 실행하거나 테이블·Seed를 준비할 필요는 없다.

실행기는 빈 로컬 포트에 임시 Backend를 시작하고 준비 완료를 기다린다. 성공·실패·사용자 중단 시 자신이 띄운 Backend를 종료한다. PostgreSQL과 DB 기록은 유지한다.

기존 셸의 export 값이 `.env`보다 우선하므로 충돌하는 설정이 없는지 확인한다. 이전 `WORKFLOW_SMOKE_BASE_URL` 설정은 사용하지 않는다.

## 3. 한 줄 실행

저장소 루트에서 실행한다.

```bash
uv run --env-file .env python scripts/workflow_smoke.py
```

PostgreSQL 기동 → 테이블 생성·필요한 001 migration → Seed 갱신 → 임시 Backend 시작·연결 → 임시 member 인증으로 Seed 고객·조직·구독·결제 조회가 진행되고 각 요청 결과가 출력된다. 이후 비용 안내를 읽고 `RUN WORKFLOW`를 직접 입력해야 진행된다. 입력이 다르거나 stdin이 닫혀 있으면 문의 생성과 진단 요청을 하지 않는다. 확인을 생략하는 옵션은 없다.

확인 후 “결제했지만 팀원을 초대할 수 없다”는 문의 **한 건**을 생성하고 pending·빈 Trace를 확인한다. `POST /support/requests/{id}/diagnose?mode=workflow`를 **한 번** 호출하고, 별도 GET으로 저장된 Diagnosis와 Trace를 재조회한다. 실제 고객 상태 변경·결제·환불 Tool은 실행하지 않는다.

진단 실패나 연결 단절 시 POST를 재시도하지 않는다. 같은 문의의 결과와 Trace를 각각 한 번 조회해 가능한 기록을 보존한다. 반복 실행은 새 문의와 추가 비용을 발생시킬 수 있으므로 실패 보고서를 먼저 확인한다.

## 4. report.md 확인

마지막에 결과 경로와 판정이 출력된다.

```text
.smoke-results/workflow-run-<고유값>/
  report.md
  created.json
  diagnosed.json
  retrieved.json
  traces.json
```

같은 폴더에는 연결·Seed 조회·진단 전 확인 응답도 저장된다. 중간 실패 시 수집된 파일만 존재한다. HTTP 상태와 소요시간, UTC 실행 시각, 로컬 revision·변경 여부, Trace 실제 모델, 실행 방식, 문의·생성자·조직, 조회 순서, LLM·Tool 횟수, 공급자 Token Usage, 최종 Diagnosis·Evidence, 실패 원인과 미검증 항목을 보고서에서 확인한다. Backend가 실제 사용하는 모델은 로컬 설정값 대신 Trace에서 확인한다.

Secret 설정값은 마스킹하고 인증 Header 등 민감 필드는 JSON 저장에서 제거한다. 결과 디렉터리·파일은 생성 시 소유자 전용 권한을 사용하며 `.smoke-results/`는 Git에서 제외된다. 고객 데이터와 모델 생성 문장은 포함되므로 외부 공유 전 검토한다.

## 5. 자동 검증과 사용자 의미 검토

| 자동 검증 | 통과 기준 |
|---|---|
| 실행 방식 | GET의 `execution_mode=workflow` |
| 고정 조회 | 고객 4회 → 정책 3회, 정확한 Tool·인자·조직/사용자·검색어 순서, 성공 관찰·참조 sequence |
| 단일 LLM | `llm_response` 정확히 1건, model·response_id 존재, 오류 없음 |
| 동적 Tool 실행 없음 | 7개 고정 조회가 단일 LLM 응답보다 먼저 실행되고 추가 Tool 실행 기록 없음 |
| 정상 종료 | `status=completed`, `error_code=null`, Diagnosis 존재 |
| Diagnosis·Evidence | 공통 Schema와 서비스 검증기로 실제 관찰 sequence·정책 ID 참조 확인 |
| Trace | 연속 sequence, 조회 step 1~7·LLM step 8, 시작·최종 결과·완료 이벤트, UTC 시각·소요시간·오류 일관성 |
| 저장 일치 | 진단 POST와 GET 전체 응답 일치 |
| 실패 보존 | 실패 발생 시 안전한 오류 코드·실패 종료 Trace·Diagnosis 부재 확인. 정상 실행에서는 미검증으로 기록 |

Trace는 공급자 요청의 tools 옵션과 거부된 function_call 원문을 저장하지 않는다. 동적 호출 검사는 기록된 실행과 Workflow 완료 계약에 근거하며, 공급자 원문을 직접 검사한 것으로 해석하지 않는다. 정상 실행으로 실패 시나리오 전체를 검증했다고 기록하지 않는다. Token Usage 누락은 미제공으로 표시하고 0으로 추정하지 않는다. 단계 소요시간, 문의 실행 시간, HTTP 시간과 Runner 전체 시간은 서로 다르다.

**Diagnosis의 의미적 정확성과 정책 해석은 자동 Pass 처리하지 않는다.** `report.md`의 체크리스트를 사용해 `traces.json` 관찰·정책과 진단을 비교한다.

- 원인·요약·다음 행동이 실제 사용자 역할, 팀원 한도, 구독·결제 상태와 부합하는가?
- Evidence의 문장이 참조 관찰을 정확히 설명하며 정책 해석이 타당한가?
- Seed 구독 만료일과 진단 시각을 고려했는가?
- `needs_information`의 질문 또는 `escalate`의 이관이 적절한가?

추가 LLM을 판정자로 호출하지 않는다. Smoke 한 건은 전체 품질·성능 평가나 Agent 대비 우열을 입증하지 않는다.

## 6. 최종 판정 및 보관

자동 검사 통과 시 전체 판정은 **의미 검토 대기**다. 사용자는 보고서에 검토자·일시·근거를 기록하고 의미 검토까지 만족할 때 최종 통과로 판정한다. 실패하거나 검증할 수 없는 항목을 Pass로 바꾸지 않는다.

종료 코드 `0`은 자동 검사 통과·의미 검토 대기, `1`은 실행/자동 검사/파일 저장 실패, `2`는 자동 검증 미완료다. 정상 실행의 실패 경로 미검증 항목은 자동 통과 조건에서 제외한다. 실행 확인 거부는 중단 사유와 실패 판정으로 기록된다.

실패 시 수집한 응답과 보고서를 보관하고 같은 문의 ID의 상태를 먼저 확인한다. 연결 단절 직후 GET에서는 아직 `running`이거나 Trace가 비어 있을 수 있다. 자동 polling·재진단·새 문의 재생성은 수행하지 않는다. 프로세스 강제 종료·지속 DB 장애에서는 Trace 유실이 가능하다. 디스크 부족·권한 오류로 보고서를 쓸 수 없으면 안전한 오류와 보존된 디렉터리 경로를 출력하지만 `report.md` 생성을 보장할 수 없다. `uv` 실행 자체가 실패한 경우도 보고서가 생성되지 않는다.

구현: [실행기](../scripts/workflow_smoke.py), [Fake 테스트](../tests/test_workflow_smoke.py). 기존 Agent Smoke 실행기에서 임시 Backend 시작·종료와 HTTP 수집·Secret 마스킹·파일 저장을 재사용하며 기존 Agent/Workflow 서비스 동작은 변경하지 않는다.
