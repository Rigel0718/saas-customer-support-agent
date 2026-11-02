# Proposal

## Why

지금 Support Agent는 고객 계정 상태(사용자, 조직, 구독, 결제)와 짧은 정책 문서 4개만 근거로 쓴다. 실제 고객 지원에서는 대부분의 답이 **제품 문서와 과거에 해결된 문의**에 있다. "이 오류는 지난달 다른 고객이 겪은 문제와 같다", "이 설정은 문서의 이 절을 따르면 된다" 같은 판단이 그렇다. 이런 근거 없이는 Agent와 Workflow 모두 정형화된 계정 진단밖에 할 수 없다. 1·2차 Evaluation이 Case 설계로 결과가 정해진 근본 이유도 여기에 있다.

이 Change는 Support Agent가 근거로 쓸 **지원 지식 베이스(Support Knowledge Base)**를 만든다. 지식 베이스는 제품 문서와 과거 지원 스레드(문의, 답변, 채택된 해결)를 출처와 무관한 공통 모델로 저장한다. 그리고 **문의 시점에 상담원이 알 수 있었던 정보만** 검색되도록 보장한다.

이 시점 보장은 운영과 평가 양쪽에 필요하다.
- **운영:** 아직 일어나지 않은 일을 근거로 답하지 않는다.
- **평가:** 과거 문의를 다시 풀 때, 그 문의의 정답이 검색 결과로 새어 들어오지 않는다.

실제 데이터가 필요하므로 첫 데이터 출처는 공개 지원 데이터가 있는 오픈소스 SaaS로 정한다. 출처는 설정으로 지정하며, 초기값은 Langfuse(LLM observability SaaS)의 GitHub Issues·Discussions와 공식 문서(MIT)다.

## What Changes

- **지식 베이스 모델:** 출처 이름(`source`)과 스냅샷 단위로 구분되는 세 테이블을 추가한다.
  - `kb_documents`: 문서
  - `kb_threads`: 지원 스레드
  - `kb_posts`: 스레드의 댓글과 답변
- **시점 범위 검색 Service:** `KnowledgeScope`(source, snapshot, as_of, 제외할 스레드)를 Application이 만들어 넘긴다.
  - 제공 기능: 문서 검색·조회, 스레드 검색·조회 (PostgreSQL full-text search)
  - as_of 이후의 스레드와 댓글은 반환하지 않는다. 스레드 상태와 채택 답변도 as_of 기준으로 다시 계산한다.
  - 기존 조직 범위(`CallerContext`)처럼, LLM 인자로는 이 범위를 바꿀 수 없다.
- **스냅샷 수집·적재:** `scripts/ingest_knowledge_base.py`를 추가한다.
  - 설정한 GitHub 저장소에서 문서, Issues, Discussions를 수집한다.
  - 결과를 `.knowledge/<source>/<snapshot_id>/`에 manifest와 함께 저장하고 DB에 적재한다.
  - 공개 GitHub 저장소라는 출처 형태 하나만 구현한다. 다른 출처용 일반화는 하지 않는다.
- **평가 Case 후보 추출:** 해결된 스레드(채택 답변이 있는 Discussion, duplicate로 닫힌 Issue)를 다음 실험의 Case 후보로 내보낸다. 정답 답변과, 답변이 참조하는 문서·스레드도 함께 내보낸다.
- **설정:** `GITHUB_TOKEN`(읽기 전용 public 권한)을 `.env.example`에 추가한다.

## 제외 범위

- 지식 베이스를 쓰는 Agent Tool, Workflow, 진단 출력 계약 (다음 Change)
- 최종 Case 선정과 GT 동결, 실험 실행과 채점 (실험 단계에서 진행)
- 임베딩과 벡터 검색, Reranker, 문서 청크 분할
- GitHub 외 출처(Zendesk, Intercom 등) 연동, 지식 베이스 쓰기 API와 관리 UI
- 기존 가상 SaaS 도메인과 1·2차 실험 코드의 변경

## Capabilities

### New Capabilities

- `support-knowledge-base`: 지원 지식 베이스의 공통 저장 모델, 시점 범위 검색, 스냅샷 수집·적재, 평가 Case 후보 추출

### Modified Capabilities

없음.

## Impact

- **예정 코드:**
  - `app/models/knowledge.py`, `app/services/knowledge.py`, `app/knowledge/`(GitHub 수집, 문서 정제, 적재)
  - `evaluation/knowledge_cases.py`(Case 후보 추출)
  - `scripts/ingest_knowledge_base.py`, `migrations/005_support_knowledge_base.sql`, 관련 테스트
- **DB:** 테이블 3개를 추가하고, 기존 테이블은 바꾸지 않는다.
- **설정:** `.gitignore`에 `.knowledge/`를 추가한다. `.env.example`에 `GITHUB_TOKEN`을 추가한다. `pyproject.toml`에 `httpx`를 직접 의존성으로 명시한다(이미 OpenAI SDK가 쓰고 있어 새 설치는 없다).
- **외부 호출:** 수집 스크립트만 GitHub API를 호출하며, 명시적 실행 플래그가 필요하다. 자동 테스트는 네트워크 없이 Fake 응답만 쓴다.
- **데이터 취급:** 수집 데이터는 실제 사용자가 쓴 공개 글이다. 로컬에만 보관하고, 작성자 로그인 대신 `author_association`만 저장한다.
