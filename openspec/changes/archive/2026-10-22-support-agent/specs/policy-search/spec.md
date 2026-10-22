# Spec Delta

## Purpose

플랜·권한·문제 해결 정책을 프롬프트와 분리하여 관리하고 문의에 관련된 문서를 간단한 검색으로 제공한다. 검색 결과의 식별자와 내용을 진단 근거로 추적하고 동일 정책·검색어에 대해 재현 가능한 결과를 보장한다.

## ADDED Requirements

### Requirement: MVP 정책 문서
시스템은 free/pro/enterprise 플랜, owner/admin/member 권한, 구독·결제 상태 및 팀원 초대 문제 해결을 다루는 별도 정책 문서를 제공해야 한다(MUST). 문서는 고정 document_id, title, keywords, content를 가져야 하고 고객별 데이터나 Secret을 포함해서는 안 된다(MUST). 정책은 기존 데이터의 상태값·member_limit 의미와 일치해야 한다(MUST).

#### Scenario: 정책 범위와 데이터 의미
- **WHEN** 플랜·권한·문제 해결 문서를 검토한다
- **THEN** 조직의 member_limit을 실제 초대 한도로 사용하고 owner/admin과 member의 초대 권한, 만료·past_due·실패/pending 결제의 의미를 설명하며 환불·계정 변경을 수행한다고 안내하지 않는다

### Requirement: 단순하고 결정적인 정책 검색
search_policy는 공백 제거 후 1~200자의 query로 문서의 title·keywords·content를 검색하여 최대 3개의 items를 반환해야 한다(MUST). 항목에는 document_id, title, content를 포함하고 같은 문서 집합·query에 대한 순서와 내용이 같아야 한다(MUST). 일치 문서가 없으면 빈 items를 반환하며 잘못된 query는 invalid_arguments로 처리해야 한다(MUST).

#### Scenario: 정책 검색 성공과 순서
- **WHEN** 동일 정책 집합에서 같은 검색어로 반복 검색하거나 점수가 같은 문서가 존재한다
- **THEN** 관련 문서가 최대 3개 반환되고 동점 결과도 항상 같은 순서다

#### Scenario: 빈 검색 결과와 잘못된 입력
- **WHEN** 관련 문서가 없는 query 또는 빈/과도한 길이/추가 인자를 입력한다
- **THEN** 각각 정상 빈 items 또는 invalid_arguments를 반환하며 일반 정책을 검색 성공처럼 대체하여 반환하지 않는다

### Requirement: 정책 근거의 추적성과 읽기 전용성
검색은 정책 문서와 고객 데이터를 수정하지 않고 진단 실행의 검색 관찰을 통해 document_id와 당시 반환한 내용을 보존해야 한다(MUST). 모델이 검색에서 받지 않은 문서 ID를 근거로 제출하면 최종 결과 검증에 실패해야 한다(MUST). 정책 파일 로딩 오류는 검색 부재로 숨기지 않고 실행 실패로 처리해야 한다(MUST).

#### Scenario: 문서 변경 이후 근거 조회
- **WHEN** 완료된 진단 이후 로컬 정책 문서가 변경되고 기존 Trace를 조회한다
- **THEN** 진단 당시 반환된 문서 ID와 내용이 Trace에 남아 기존 근거를 확인할 수 있다

#### Scenario: 없는 정책 근거 및 로딩 실패
- **WHEN** 모델이 반환받지 않은 문서 ID를 인용하거나 정책 파일을 읽을 수 없다
- **THEN** 각각 invalid_output 또는 tool_error로 종료하고 완료된 정상 진단으로 저장하지 않는다
