# Tasks

완료 기준은 [Design](design.md)과 [support-knowledge-base 명세](specs/support-knowledge-base/spec.md)를 따른다. 자동 테스트는 네트워크 없이 Fake 응답만 쓴다. 실제 수집은 사용자가 `GITHUB_TOKEN`을 설정한 뒤 별도로 실행한다.

## 1. 지식 베이스 모델

- [x] 1.1 `app/models/knowledge.py`(세 테이블, generated tsvector, GIN 인덱스, source·스냅샷 단위 고유 키)와 `migrations/005_support_knowledge_base.sql`을 추가한다. 테스트: 신규 초기화와 migration 구조 일치, 기존 행 보존, 모델 등록 수 갱신.

## 2. 시점 범위 검색

- [x] 2.1 `app/services/knowledge.py`: `KnowledgeScope`, `search_documents`, `get_document`, `search_threads`, `get_thread`. 테스트: as_of 이후 스레드·댓글 차단, 상태·채택 재계산, 라벨 미반환, 제외·미래·없는 스레드 동일 not_found, 결정적 정렬, limit·잘림, 읽기 전용.

## 3. 수집과 적재

- [x] 3.1 `app/knowledge/github.py`: Issues·댓글 REST와 Discussions GraphQL 수집, 페이지네이션, rate limit 재시도, PR 제외, author_association만 저장. 테스트: Fake transport로 페이지·PR 혼합·재시도·incomplete·토큰 비노출.
- [x] 3.2 `app/knowledge/documents.py`: T0 이전 commit 조회, Git tree 목록과 raw 파일로 문서 루트의 MD/MDX만 수집, 정제. 테스트: frontmatter·import 제거, 비문서 파일 무시, 경로 탈출 무시, raw 요청에 토큰 없음, 0건·목록 잘림 실패.
- [x] 3.3 `app/knowledge/loader.py`: JSONL → 세 테이블 upsert. 테스트: 재적재 동일, 스냅샷·출처 공존.
- [x] 3.4 `scripts/ingest_knowledge_base.py`: 출처 설정(기본 Langfuse), `--allow-network`, `--load`, T0/T1, 스냅샷 버전 디렉터리, manifest. 테스트: 플래그 없는 중단, 덮어쓰기 거부, manifest와 파일 일치.

## 4. 평가 Case 후보

- [x] 4.1 `evaluation/knowledge_cases.py`: Discussion·duplicate Issue 후보, 링크 정규화, 스냅샷 밖 문서 표시. 테스트: 명세 시나리오의 링크·제외 사례.

## 5. 통합

- [x] 5.1 `.gitignore`에 `.knowledge/`, `.env.example`에 `GITHUB_TOKEN`, `pyproject.toml`에 `httpx` 직접 의존성을 추가한다.
- [x] 5.2 `uv run pytest` 전체 회귀를 실행한다.
- [x] 5.3 (토큰 설정 후, 사용자 승인) 실제 수집을 1회 실행하고 건수와 후보 수를 보고한다. (langfuse-20261102-v1/v2, 답변 가능성 표본 측정 `.evaluation-results/kb-answerability-v1`)

## 6. 첫 수집과 측정 결과 반영

- [x] 6.1 문서 버전: `kb_documents`에 version·valid_from·published_on을 추가하고 분기 버전과 날짜 있는 문서를 수집한다. 검색 범위를 D2·D3 규칙으로 바꾼다. 테스트: 버전 선택, 발표 날짜 필터, 미래 버전 not_found, 적재 재현.
- [x] 6.2 답글과 봇 여부: Discussion 댓글 답글(parent_post_id)과 author_is_bot을 수집·저장한다. 테스트: 답글 채택 답변, 잘림 표시, 봇 판별, 개인정보 미저장.
- [x] 6.3 후보 추출: 답글 채택 답변 포함, 봇 답변 제외, 질문 시점 문서 기준 missing 표시. 테스트: 명세 시나리오.
- [x] 6.4 재수집·적재·후보 추출 후 건수를 보고한다. (langfuse-20261102-v3)
