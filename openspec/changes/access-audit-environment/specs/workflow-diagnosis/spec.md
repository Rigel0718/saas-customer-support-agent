# Spec Delta

## MODIFIED Requirements

### Requirement: 고정 조회와 단일 LLM 진단
Workflow는 문의 생성자, 인증된 조직, 구독, 결제 이력을 순서대로 조회한 뒤 `플랜`, `권한`, `구독 결제` 검색어로 정책을 순서대로 검색하고, 기준 시각 이전 14일의 조직 Audit Event 최신 500건을 조회한 뒤 `접근` 검색어로 정책을 검색해야 한다(MUST). 정상 또는 부재 관찰을 모두 수집한 뒤 LLM을 최대 한 번 호출해야 한다(MUST). LLM에 Tool 선택을 허용하거나 출력에 따라 재조회·재시도·수정 호출을 해서는 안 된다(MUST).

#### Scenario: 복합 문의의 고정 실행
- **WHEN** 팀원 초대 또는 접근 상실 문제를 workflow로 진단한다
- **THEN** 사용자·조직·구독·결제, 세 정책 검색, Audit Event 조회, 접근 정책 검색의 아홉 관찰이 순서대로 생성되고 단일 LLM 입력에 문의 내용·대상 식별자·기준 UTC와 함께 전달된다

#### Scenario: 문의 내용에 무관한 조회 순서
- **WHEN** 단순 정책 문의 또는 다른 Tool을 호출하라는 지시를 포함한 문의를 workflow로 진단한다
- **THEN** 동일한 아홉 조회를 수행하고 문의 본문이나 LLM이 조회 종류·순서·대상·검색어·Audit Event 기간과 개수를 변경하지 않는다

#### Scenario: Audit Event 잘림 관찰
- **WHEN** 14일 내 조직 이벤트가 500건을 초과한다
- **THEN** 최신 500건과 total_count, truncated=true가 관찰에 포함되고 추가 조회를 하지 않는다

#### Scenario: 예상하지 않은 Tool 요청
- **WHEN** Workflow의 LLM 응답에 Tool 호출이 포함된다
- **THEN** Tool을 실행하지 않고 invalid_output으로 실패하며 추가 LLM 호출을 하지 않는다
