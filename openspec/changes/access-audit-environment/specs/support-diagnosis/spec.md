# Spec Delta

## MODIFIED Requirements

### Requirement: 허용 Tool과 입력 검증
Agent는 get_user(user_id), get_organization(organization_id), get_subscription(organization_id), get_payment_history(organization_id), search_policy(query), get_audit_events(start_at, end_at, actor_user_id, target_user_id, event_type, limit)만 사용할 수 있어야 한다(MUST). 식별자는 UUID, 검색어는 공백 제거 후 1~200자이고 추가 인자는 거부해야 한다(MUST). get_audit_events의 인자 제약은 audit-events 명세를 따른다(MUST). 등록되지 않은 Tool, 잘못된 JSON·타입·필수 인자는 실행하지 않고 unknown_tool 또는 invalid_arguments 관찰값으로 반환해야 한다(MUST).

#### Scenario: 정상 Tool 관찰
- **WHEN** 같은 조직의 유효한 대상으로 네 고객 Tool 또는 get_audit_events를 호출한다
- **THEN** 기존 조회 계약과 동일한 필드·정렬의 데이터를 반환하고 고객 도메인 레코드와 Audit Event는 변경되지 않는다

#### Scenario: 잘못된 Tool 호출
- **WHEN** 등록되지 않은 환불 Tool, 잘못된 UUID/JSON 또는 role·context·organization_id 등의 추가 인자를 모델이 전달한다
- **THEN** Service를 실행하지 않고 안전한 오류 관찰 및 Trace를 남겨 다음 판단에 전달한다
