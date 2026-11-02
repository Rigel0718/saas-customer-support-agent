# Tasks

완료 기준은 [Design](design.md)과 [knowledge-answer 명세](specs/knowledge-answer/spec.md)를 따른다. 자동 테스트는 Fake LLM Client와 테스트 PostgreSQL만 쓴다.

## 1. 공통 기반

- [x] 1.1 `app/diagnosis/llm.py`: `call_model`에 출력 스키마와 스키마 이름 인자를 추가한다(기본값 `DiagnosisDTO`). 테스트: 기본 호출의 요청 내용 불변, 지정한 스키마 반영.
- [x] 1.2 `app/schemas/support.py`: 실패 단계·사유 `citation_reference`(`invalid_citation_reference`, `unobserved_citation`), `workflow_queries`(`invalid_queries`), 지식 답변 JSON·스키마 오류를 추가한다. 테스트: 허용 쌍과 거부 쌍.

## 2. 지식 베이스 Tool

- [x] 2.1 `app/diagnosis/knowledge_tools.py`: 네 Tool의 strict 인자 schema, Tool 정의(설명에 검색어 작성 규칙), `KnowledgeScope`를 받는 관찰 실행 함수(Timeout·Trace는 기존 순서). 테스트: 범위 인자 거부, 미래·제외 스레드 not_found 관찰, ref 형식, rank 미노출, DB 오류 실패, Trace 기록.

## 3. 출력 계약

- [x] 3.1 `app/schemas/knowledge_answer.py`: `Citation`, `KnowledgeAnswerDTO`, `KnowledgeQueriesDTO`, 결과 타입.
- [x] 3.2 `app/services/knowledge_answer.py`: 답변 검증(JSON·스키마, 인용의 관찰 연결·ref·source, answered 인용 필수). 테스트: 명세의 인용 시나리오 4개.

## 4. 실행 경로

- [x] 4.1 `app/agent/knowledge.py`: Agentic RAG Loop. 테스트: 검색어 수정 후 답변, Step·Tool 호출 한도, 잘못된 call_id.
- [x] 4.2 `app/workflow/knowledge.py`: 검색어 생성 → 고정 검색·조회 → 답변. 테스트: 결정적 Tool 순서·인자, 조회 3+3 상한과 중복 제거, LLM 2회, 잘못된 검색어, 답변 단계 function call.
- [x] 4.3 `answer_question` 진입점: 모드 분기, 검증, 완료·실패 Trace. 테스트: 모드별 동일 계약, Timeout·LLM 오류 분류 유지.

## 5. 검증

- [x] 5.1 `uv run pytest` 전체 회귀를 실행한다. 기존 계정 진단 테스트가 그대로 통과해야 한다.
- [x] 5.2 (사용자 승인 후) 개발 DB 스냅샷으로 Case 1건을 두 모드에서 실제 모델로 실행해 Trace와 답변을 확인한다. 자동 테스트와 분리한다.
  - 2026-11-02 1차: Agent 완료, Workflow는 LLM 요청 Timeout(20초)으로 2회 실패. 실험 요청 Timeout을 60초로 정하고 Workflow 재실행 → 완료(약 28초, 정답 근거 `log-levels` 인용). 기록: `.evaluation-results/kb-answer-smoke-v1/summary.md`.
