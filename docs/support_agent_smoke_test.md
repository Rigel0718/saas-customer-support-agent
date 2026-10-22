# Support Agent Smoke Test

## 1. 실행 전 준비

이 테스트는 실제 OpenAI Responses API를 사용한다. 사용자가 실행하며, 진단 한 건 안에서 여러 모델 호출의 비용이 발생할 수 있다. 전체 품질·성능 평가나 Workflow 비교는 포함하지 않는다.

저장소 루트에서 실행한다. `uv`, Docker Compose v2와 실행 중인 Docker가 필요하다. 로컬 `.env`에는 [.env.example](../.env.example)을 기준으로 다음 값을 준비한다.

- `POSTGRES_PASSWORD`, `POSTGRES_PORT`, `DATABASE_URL`: Compose 개발 DB 연결과 일치해야 한다. 기존 volume의 비밀번호는 `.env`를 바꿔도 바뀌지 않는다.
- `DEV_BEARER_TOKENS`: 기존 JSON 매핑을 그대로 사용한다. 예제의 owner 매핑도 가능하다. 스크립트가 실행 중에만 member용 토큰을 추가하며 `.env`는 수정하지 않는다.
- `OPENAI_API_KEY`, `OPENAI_MODEL`: 실제 계정에서 사용 가능한 Key·모델.
- `AGENT_MAX_STEPS`, `AGENT_MAX_TOOL_CALLS`, `AGENT_TIMEOUT_SECONDS`: 기본값 8 / 16 / 60초. `TEST_DATABASE_URL`은 사용하지 않는다.

**개발용 DB에서만 실행한다.** Seed의 고정 PK 데이터를 현재 UTC 기준으로 갱신하며, 기존 Seed 수정값을 덮어쓴다. 다른 PK나 기존 문의는 삭제하지 않는다. 기존 테이블 구조를 바꾸는 migration은 수행하지 않는다.

이미 셸에 export한 환경변수는 `.env`보다 우선하므로 설정이 충돌한다면 해제한다. Secret은 터미널이나 보고서에 직접 붙여 넣지 않는다.

## 2. 한 번에 실행

```bash
uv run --locked --env-file .env python -m scripts.support_agent_smoke
```

별도 서버 실행, 토큰 입력, curl 요청, 결과 양식 작성은 필요 없다. 이 명령을 실행하면 실제 API 비용이 발생할 수 있다. Codex는 실제 호출을 실행하지 않는다.

스크립트는 다음 순서로 진행한다.

1. 설정 확인 → PostgreSQL 기동·healthy 대기 → 테이블 생성 → Seed 갱신.
2. 비어 있는 로컬 포트에 임시 Backend 실행. 기존 8000번 서버와 별개로 실행한다.
3. member 사용자·조직·구독·결제 조회 → 문의 생성 및 pending·빈 Trace 확인.
4. “결제했지만 팀원을 초대할 수 없다”는 문의를 **한 번 진단**.
5. 별도 GET으로 저장 결과·Trace 재조회 → 자동 검사 → 아래 5번 양식의 보고서 저장.
6. 스크립트가 시작한 Backend 종료. PostgreSQL과 DB 기록은 유지한다.

진단 오류나 timeout에도 POST를 재시도하지 않는다. 가능한 경우 GET으로 최종 상태를 수집하고 실패 보고서를 작성한다. 다시 명령을 실행하면 **새 문의**로 테스트하며 추가 비용이 발생할 수 있다. 실행 중 강제 종료나 지속 DB 장애에서는 `running` 상태가 남을 수 있다.

## 3. 보고서 확인

마지막에 다음 파일의 실제 경로가 출력된다.

```text
.smoke-results/run-<고유값>/report.md
```

같은 폴더의 JSON 파일에는 고객 조회, 문의 생성, 진단 POST, 최종 GET, Trace 응답이 저장된다. 폴더는 Git에서 제외되며 Secret 설정값과 토큰은 저장 전에 마스킹한다. 고객 데이터는 포함되므로 공유 전 확인한다. 모델 응답·오류 원문이나 서버 로그는 별도로 수집하지 않는다.

DB 준비나 서버 시작에 실패해도 스크립트가 실행된 이후라면 보고서에 실패 단계를 기록한다. 원본 예외·프로세스 출력을 저장하지 않으므로 DB 비밀번호·스키마 문제 등 구체적 원인은 로컬에서 확인해야 한다. `uv` 의존성 설치 실패나 프로세스 강제 종료처럼 스크립트가 시작·종료 처리를 할 수 없는 경우에는 보고서 생성을 보장하지 않는다.

## 4. 자동 판정 범위

| ID | 자동 확인 |
|---|---|
| ST-01 | 성공 LLM 응답의 model·response_id·오류 여부 |
| ST-02 | 성공 고객 조회·정책 검색, Tool 인자·관찰 sequence, 이후 step에서 추가 Tool 선택 |
| ST-03 | 정상 완료·진단 형식·저장·실제 관찰 및 정책 ID 근거 참조 |
| ST-04 | Trace sequence, 시작·LLM·Tool·최종 결과·정상 종료, 소요시간·시각 등 필드 |
| ST-05 | 진단 POST와 GET 전체 일치, 정상 상태·Diagnosis·시각 순서·종료 Trace |

**ST-03의 설명과 관찰 내용이 의미상 일치하는지는 자동으로 확정하지 않는다.** 구조 검사가 통과하면 ST-03은 `미검증`으로 두고, 보고서에 진단 전문과 검토 안내를 넣는다. `traces.json`의 관찰·정책과 비교해서 타당하면 ST-03을 Pass로 바꾸고 전체 판정을 통과로 기록한다. 추가 LLM을 판정자로 호출하지 않는다.

ST-02에서 Tool을 한 step에만 호출했다면 후속 Tool 선택은 `미검증`이다. 모델 문구나 Tool 순서를 고정하지 않으며 `needs_information`·`escalate`도 유효한 정상 결과일 수 있다. usage는 제공된 호출만 합산하며 미제공 호출 수도 표시한다.

종료 코드는 `0`이면 자동 검사 통과·의미 검토 대기, `1`이면 실행 또는 검사 실패, `2`이면 자동 검증 미완료다. **종료 코드 0이 다섯 항목 모두 Pass라는 뜻은 아니다.** 보고서의 검토 상태를 확인한다.

테스트 후 다른 작업이 DB를 사용하지 않을 때만 필요에 따라 실행한다. volume은 삭제하지 않는다.

```bash
docker compose stop postgres
```

## 5. 결과 기록 양식

스크립트가 아래 항목을 `report.md`에 자동으로 채운다. 의미 검토 결과만 실행 후 확인한다. Secret과 오류 원문을 붙이지 않는다.

| 항목 | 사용자 기록 |
|---|---|
| 테스트 일시 및 시간대 / 시작·종료 | |
| 테스트 수행자 | |
| 코드 revision (`git rev-parse --short HEAD`) / 로컬 변경 여부 | |
| 설정한 `OPENAI_MODEL` | |
| Trace에 기록된 실제 `model` | |
| `AGENT_MAX_STEPS` / `AGENT_MAX_TOOL_CALLS` / `AGENT_TIMEOUT_SECONDS` | |
| 문의 ID / 생성자 UUID / 조직 UUID | |
| Seed 실행 시각 / 진단 당시 UTC / Seed 만료일 | |
| 진단 HTTP 코드 / 최종 status / outcome / error_code | |
| LLM 호출 수 / Tool 호출 수 / 주요 Tool 순서 | |
| Trace usage 요약 (미제공 시 미제공으로 기록) | |
| 응답 파일 보관 위치 (공유 시 데이터 검토) | |

| ID | 항목 | Pass / Fail / 미검증 | 주요 확인 결과·참조 sequence |
|---|---|---|---|
| ST-01 | Responses API 연결 | | |
| ST-02 | Tool Calling·후속 선택 | | |
| ST-03 | 진단 생성·저장·근거 | | |
| ST-04 | AgentTrace 기록 | | |
| ST-05 | 종료 상태·Diagnosis 일관성 | | |

- 관찰과 최종 설명이 일치하는지에 대한 검토:
- 실패 단계 및 확인된 원인 (추정은 추정이라고 표기):
- 미해결 사항 및 미검증 범위:
- 재실행 여부·사유 및 새 문의 ID (추가 호출 비용 고려):
- 전체 판정 (다섯 항목 모두 Pass일 때 정상 시나리오 통과):

## 6. 구현 근거

- [실행 스크립트](../scripts/support_agent_smoke.py), [자동화 검증](../tests/test_smoke_runner.py)
- [MVP 범위](mvp.md), [Archive된 Change](../openspec/changes/archive/2026-10-22-support-agent/proposal.md)
- [진단 계약](../openspec/specs/support-diagnosis/spec.md), [문의 계약](../openspec/specs/support-requests/spec.md)
- [설정 예시](../.env.example), [Endpoint](../app/api/support.py), [진단 및 근거 검증](../app/services/support.py)
