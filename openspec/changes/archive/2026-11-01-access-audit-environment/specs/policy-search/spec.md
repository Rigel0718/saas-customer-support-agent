# Spec Delta

## MODIFIED Requirements

### Requirement: MVP 정책 문서
시스템은 free/pro/enterprise 플랜, owner/admin/member 권한, 구독·결제 상태, 팀원 초대 문제 해결 및 멤버 관리 접근 조건을 다루는 별도 정책 문서를 제공해야 한다(MUST). 문서는 고정 document_id, title, keywords, content를 가져야 하고 고객별 데이터나 Secret을 포함해서는 안 된다(MUST). 정책은 기존 데이터의 상태값·member_limit 의미와 일치해야 한다(MUST).

#### Scenario: 정책 범위와 데이터 의미
- **WHEN** 플랜·권한·문제 해결 문서를 검토한다
- **THEN** 조직의 member_limit을 실제 초대 한도로 사용하고 owner/admin과 member의 초대 권한, 만료·past_due·실패/pending 결제의 의미를 설명하며 환불·계정 변경을 수행한다고 안내하지 않는다

#### Scenario: 접근 정책과 Audit Event 해석
- **WHEN** `접근` 검색어로 정책을 검색한다
- **THEN** workspace_access 문서가 반환되며 멤버 관리에 owner/admin 역할과 활성 구독이 필요함, Audit Event는 변경 시점·행위자 근거이며 현재 상태는 고객 데이터 조회로 확인함, 관찰되지 않은 변경을 추정하지 않음을 설명한다
