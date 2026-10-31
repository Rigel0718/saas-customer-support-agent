# Tasks

완료 기준은 [Design](design.md)과 각 spec delta를 따른다. 자동 테스트는 Fake Client만 사용하고, 실제 OpenAI 파일럿 실행은 사용자 승인 후 별도로 진행한다.

## 1. Audit Event 데이터와 조회

- [x] 1.1 `AuditEvent` ORM, event_type CHECK, 조직 FK, `(organization_id, occurred_at)` 인덱스, `migrations/004_audit_events.sql`을 추가한다. 테스트: 허용 외 종류·없는 조직 거부, 신규 초기화 구조와 migration 적용 후 기존 행 보존.
- [x] 1.2 `customer.get_audit_events`와 DTO(items/total_count/truncated)를 추가한다. 테스트: 필터 조합, 정렬과 동점 처리, limit 잘림, 다른 조직 이벤트·사용자 UUID 미노출, 조회 전후 데이터 불변.

## 2. Agent Tool과 Workflow

- [x] 2.1 `ARGUMENTS`에 `get_audit_events`(nullable 필수 필드, limit 1~50, 기간 역전 거부, 추가 인자 거부)와 Tool 설명을 추가한다. 테스트: strict schema 형태, 정상 관찰과 Trace, invalid_arguments 경로.
- [x] 2.2 Workflow에 Audit Event(14일, 500건)와 `접근` 정책 검색 단계를 추가한다. 기존 Workflow 테스트의 일곱 조회 기대값을 아홉으로 갱신하고, 잘림 관찰과 LLM 1회 유지를 검증한다.
- [x] 2.3 `policies/workspace_access.json`을 추가한다. 테스트: `접근` 검색 결과에 workspace_access 문서가 포함되고 기존 검색어 결과 순위는 유지된다.

## 3. 파일럿 Fixture와 실행

- [x] 3.1 `evaluation/access_pilot.py`에 A01~A04 Case와 결정적 노이즈 생성기를 구현한다. 테스트: 같은 seed는 같은 결과, 다른 seed는 다른 결과, 원인 이벤트 보존, 요청자 대상 혼동 이벤트 없음, 현재 상태와 원인 이벤트의 일치.
- [x] 3.2 `scripts/access_pilot.py`에서 격리 schema 생성, Fixture 삽입, 모드 교대 실행, executions.json 저장을 구현한다. 테스트: Fake Client로 2 Case × 2 N × 2 모드 실행 시 8건 저장, 기록 필드 존재, 잘못된 DB 선택 거부.

- [x] 3.3 `evaluation/data/access_gt.json`(access-gt-v1)과 검증 로더를 추가하고 manifest에 GT 출처를 기록한다. 테스트: GT·Fixture 일치, 일관되지 않은 GT 거부.
- [x] 3.4 (실행 후) access-gt-v1 기준으로 실행별 사람 라벨을 작성한다. Claude가 Trace 근거와 함께 초안을 만들고 사용자가 확정한다. (access-48-v1, access-48-v2 확정)

## 4. 통합 확인

- [x] 4.1 `uv run pytest` 전체 회귀를 실행한다.
- [x] 4.2 `docs/evaluation_runbook.md`에 파일럿 실행 방법과 비용 주의를 짧게 추가한다.
- [x] 4.3 (사용자 승인 후) 실제 API로 N=50/5000 × A01~A04 × 두 모드 × 1회 파일럿을 실행하고, Trace를 함께 검토한다. (access-16-v1/v2, 이후 3회 반복 access-48-v1/v2)
- [x] 4.4 2차 결론을 `docs/evaluation/second-evaluation-conclusion.md`에 정리한다.
