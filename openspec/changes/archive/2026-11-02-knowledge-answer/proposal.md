# Proposal

## Why

`support-knowledge-base` Change로 문서와 과거 지원 스레드를 문의 시점(as_of) 범위로 검색할 수 있게 되었다. 하지만 아직 이 지식 베이스를 근거로 **답변을 만드는 실행 경로**가 없다. Agent와 Workflow 모두 계정 진단용 Tool과 출력 계약(`DiagnosisDTO`)만 갖고 있다.

다음 실험의 질문은 하나다. **"검색 결과를 보고 다음 행동을 바꾸는 것(Agent)이, 한 번 정한 검색을 고정 실행하는 것(Workflow)보다 지원 답변에 유리한가."** 이를 비교하려면 두 실행 경로가 같은 지식 베이스 Service, 같은 모델, 같은 출력 계약, 같은 실행 예산을 써야 한다.

검색 상한 점검(`.evaluation-results/kb-retrieval-probe-v1`)에서 질문을 기계적으로 넣은 검색은 정답 근거의 43%만 찾았고, 짧은 키워드 검색어로는 스레드 근거도 상위에 들어왔다. 따라서 차이는 검색 엔진이 아니라 **검색어를 정하고 고치는 방식**에서 날 것으로 보고, 검색 계층은 그대로 둔다.

## What Changes

- **지식 베이스 Agent Tool 4개:** `search_documents`, `get_document`, `search_threads`, `get_thread`. 기존 `app/services/knowledge.py`를 그대로 호출한다. 검색 범위(`KnowledgeScope`)는 Application이 정하며 Tool 인자로 바꿀 수 없다.
- **지식 답변 출력 계약 `KnowledgeAnswerDTO`:** outcome(answered / insufficient_knowledge), answer, citations. 인용은 이번 실행의 실제 Tool 관찰(trace_sequence)과 그 관찰에 나온 문서 경로·스레드 번호만 허용한다.
- **Agent 실행 경로(Agentic RAG):** 기존 Agent Loop 구조로, 결과를 보며 검색어를 고치고 문서·스레드를 따라간다.
- **Workflow 실행 경로(고정 RAG, 검색어 생성형):** LLM이 검색어 1~3개를 한 번 생성 → Application이 고정 규칙으로 검색·조회 → LLM이 답변. LLM 호출은 2회로 고정한다.
- **공통 진입점:** `answer_question(session, scope, question, mode, settings, ...)`이 실행 모드와 관계없이 같은 검증·Trace·실패 분류를 적용한다.
- **공통 LLM 호출의 출력 스키마 지정:** `call_model`이 출력 JSON Schema를 인자로 받는다. 기본값은 기존 `DiagnosisDTO`라 기존 진단 동작은 바뀌지 않는다.

## 제외 범위

- 실험 실행기, 채점, 결과 집계 (Change 이후 `evaluation/`·`scripts/`의 실험 스크립트로 진행)
- HTTP API, 지식 답변의 DB 저장, 기존 `SupportRequest` 흐름과의 통합
- 계정 진단 Tool과 지식 베이스 Tool의 결합
- 검색 개선(벡터·하이브리드 검색, Reranker, 청크 분할, 검색어 자동 확장)
- 기존 계정 진단 Agent·Workflow의 동작 변경

## Capabilities

### New Capabilities

- `knowledge-answer`: 지식 베이스 Tool, 지식 답변 출력 계약과 인용 검증, Agent·Workflow 지식 답변 실행

### Modified Capabilities

없음. `call_model`의 출력 스키마 인자는 기본값으로 기존 동작을 유지하는 내부 변경이다.

## Impact

- **예정 코드:**
  - `app/schemas/knowledge_answer.py`: 출력 계약
  - `app/diagnosis/knowledge_tools.py`: Tool 인자·실행·관찰
  - `app/agent/knowledge.py`, `app/workflow/knowledge.py`: 두 실행 경로
  - `app/services/knowledge_answer.py`: 진입점과 출력 검증
  - `app/diagnosis/llm.py`: 출력 스키마 인자
  - `app/schemas/support.py`: 실패 단계·사유 값 추가
  - 관련 테스트
- **DB·설정:** 변경 없음. 기존 `AGENT_MAX_STEPS`, `AGENT_MAX_TOOL_CALLS`, `AGENT_TIMEOUT_SECONDS`, `WORKFLOW_TIMEOUT_SECONDS`, `LLM_REQUEST_TIMEOUT_SECONDS`를 그대로 쓴다.
- **외부 호출:** 자동 테스트는 Fake LLM Client만 쓴다. 실제 OpenAI 호출은 사용자 승인 후 별도로 실행한다.
