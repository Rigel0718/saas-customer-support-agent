# support-knowledge-base Specification

## Purpose
Support Agent가 근거로 쓰는 지원 지식 베이스를 제공한다. 제품 문서와 과거 지원 스레드를 출처와 무관한 공통 형태로 저장하고, 문의 시점에 알 수 있었던 정보만 검색되도록 보장한다. 재현 가능한 스냅샷 수집·적재와 평가용 Case 후보 추출을 함께 제공한다.

## Requirements

### Requirement: 출처와 무관한 지식 베이스 모델
시스템은 문서(`kb_documents`), 지원 스레드(`kb_threads`), 스레드 댓글·답글(`kb_posts`)을 출처 이름과 스냅샷 단위로 저장해야 한다(MUST). 문서는 버전, 버전 시작 시각, 경로의 발표 날짜를 가져야 하고, 댓글은 상위 댓글과 봇 작성 여부를 가져야 한다(MUST). 스레드는 kind, 제목, 본문, category, state, 닫힌 시각, 작성 시각, 채택 답변을 가져야 한다(MUST). 작성자 로그인·이메일·아바타는 저장하지 않고 author_association과 봇 여부만 저장해야 한다(MUST). 같은 스냅샷을 다시 적재하면 행과 개수가 같아야 하고, 다른 스냅샷이나 출처의 행을 바꾸지 않아야 한다(MUST).

#### Scenario: 재적재와 스냅샷 공존
- **WHEN** 스냅샷 A를 두 번 적재하고 스냅샷 B를 적재한다
- **THEN** A의 행은 첫 적재와 같고 B의 행은 별도로 존재한다

#### Scenario: 개인정보 최소화
- **WHEN** 작성자 로그인·이메일·아바타가 포함된 수집 응답을 적재한다
- **THEN** 저장된 스레드와 댓글에는 author_association과 author_is_bot만 있다

#### Scenario: migration
- **WHEN** 기존 데이터가 있는 테스트 schema에 005 migration을 적용한다
- **THEN** 세 테이블과 검색 인덱스가 ORM 초기화 결과와 같은 구조로 생기고 기존 테이블의 행은 바뀌지 않는다

### Requirement: 문의 시점 범위가 강제된 검색
검색·조회 Service는 Application이 만든 `KnowledgeScope`(source, snapshot_id, as_of, 제외 스레드)를 받아야 하며, 이 값은 검색 인자로 바꿀 수 없어야 한다(MUST). as_of 이후에 작성된 스레드와 댓글, 그리고 제외 스레드는 어떤 검색어나 식별자로도 반환해서는 안 된다(MUST). 스레드의 state, 닫힌 시각, 채택 답변은 as_of 기준으로 다시 계산하고, 라벨은 반환하지 않아야 한다(MUST). 범위 밖 스레드와 존재하지 않는 스레드는 같은 not_found여야 한다(MUST).

#### Scenario: 미래 정보 차단
- **WHEN** as_of 이전에 열렸다가 이후에 닫히고, 이후에 댓글과 채택 답변이 달린 스레드를 검색·조회한다
- **THEN** open 상태로 반환되며 as_of 이후의 댓글과 채택 정보는 포함되지 않는다

#### Scenario: 문서 버전 선택
- **WHEN** 분기 버전이 여러 개이고 날짜 있는 changelog가 as_of 전후에 있는 상태에서 문서를 검색·조회한다
- **THEN** 날짜 없는 문서는 as_of 이전 가장 최근 버전의 내용만, 날짜 있는 문서는 as_of 날짜보다 앞선 발표만 반환되고 미래 버전·발표는 not_found다

#### Scenario: 자기 스레드와 미래 스레드
- **WHEN** 제외 스레드나 as_of 이후 스레드의 번호로 조회하거나, 그 제목과 정확히 일치하는 검색어로 검색한다
- **THEN** 조회는 존재하지 않는 번호와 같은 not_found이고 검색 결과에는 나타나지 않는다

#### Scenario: 검색 계약
- **WHEN** 문서와 스레드를 검색하고 조회한다
- **THEN** 결과는 ts_rank_cd 내림차순과 식별자 오름차순으로 결정적으로 정렬되고, limit은 10 이하이며, 문서 본문 12,000자와 댓글 30개를 넘으면 잘림이 표시되고, 데이터는 변경되지 않는다

### Requirement: 재현 가능한 스냅샷 수집과 적재
수집 스크립트는 `--allow-network` 플래그가 있을 때만 외부 API를 호출해야 한다(MUST). 설정한 출처의 문서 버전들, Issues(Pull Request 제외)와 댓글, Discussions와 댓글·답글·채택 답변을 `.knowledge/<source>/<snapshot_id>/`에 저장하고, 기존 디렉터리를 덮어쓰지 않아야 한다(MUST). manifest에는 저장소, 문서 버전별 commit SHA, T0, T1, 수집 시각, 건수, 파일별 SHA-256, 상태를 기록해야 한다(MUST). 토큰은 환경변수로만 읽고 로그·manifest·오류 출력에 포함해서는 안 된다(MUST).

#### Scenario: Fake 응답으로 수집
- **WHEN** 페이지가 여러 개이고 Pull Request가 섞인 Fake 응답으로 수집한다
- **THEN** Issue만 저장되고, 모든 페이지의 댓글과 Discussion 채택 답변이 보존되며, manifest의 건수와 SHA-256이 파일과 일치한다

#### Scenario: 네트워크 허용 없음과 비밀 보호
- **WHEN** `--allow-network` 없이 실행하거나, 합성 토큰으로 실행하다가 API 오류가 재시도 후에도 계속된다
- **THEN** 전자는 네트워크 호출 전에 중단하고, 후자는 incomplete 상태를 기록하며, 어떤 출력과 파일에도 토큰 문자열이 없다

### Requirement: 시점별 문서 버전 수집
수집기는 T0부터 수집일까지 분기 경계마다 그 이전 마지막 commit으로 날짜 없는 문서의 버전을 만들고, 최신 commit에서 경로에 날짜가 있는 문서(changelog, blog)를 발표 날짜와 함께 받아야 한다(MUST). 각 버전은 문서 루트 아래 Markdown/MDX 파일만 가져와야 하며, 저장소의 다른 자산은 받지 않아야 한다(MUST). 공개 파일 호스트에는 토큰을 보내지 않아야 한다(MUST). frontmatter와 import·export 구문을 제거하고, 경로 탈출 항목은 무시해야 한다(MUST). 문서가 0건이거나 파일 목록이 잘리면 수집을 실패시켜야 한다(MUST).

#### Scenario: 문서 파일만 수집
- **WHEN** 파일 목록에 문서, 이미지, README, 같은 사이트 경로로 겹치는 문서가 섞여 있다
- **THEN** 문서 루트의 Markdown/MDX만 토큰 없이 받고, 겹치는 경로는 이름순 첫 파일만 쓰며, 정제된 제목과 본문이 버전과 함께 저장된다

#### Scenario: 빈 문서 수집 방지
- **WHEN** 문서 루트에 해당하는 파일이 없거나 파일 목록이 잘린 응답을 받는다
- **THEN** docs_not_found 또는 docs_tree_truncated로 실패하고 스냅샷은 incomplete로 기록된다

### Requirement: 평가 Case 후보 추출
후보 추출기는 기간 [T0, T1]에 작성된 Discussion 중 봇이 아닌 작성자의 채택 답변(답글 포함) 본문이 수집된 것과, duplicate로 닫히고 원본 번호를 뽑을 수 있는 Issue를 후보로 내보내야 한다(MUST). 각 후보는 문의 식별자·작성 시각·본문, 정답 텍스트, 답변 작성자 author_association, 정답이 참조하는 문서 path와 스레드 목록, 스냅샷에 없는 문서 링크 여부를 포함해야 한다(MUST).

#### Scenario: 링크 정규화
- **WHEN** 채택 답변에 문서 사이트 링크, `#123`, 이슈 URL, 스냅샷에 없는 문서 링크가 섞여 있다
- **THEN** 문서 path와 스레드 참조로 정규화되고 스냅샷에 없는 문서는 별도로 표시된다

#### Scenario: 후보 제외
- **WHEN** 기간 밖이거나, 채택 답변이 없거나, 채택 답변 작성자가 봇이거나, 원본 번호를 찾을 수 없는 duplicate 이슈다
- **THEN** 후보에 포함하지 않는다
