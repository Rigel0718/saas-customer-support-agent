# Agent / Workflow Evaluation 실행 안내

이 평가는 초대 실패 5개 Case에서 현재 두 구현의 시스템 수준 Trade-off를 비교한다. Fake 테스트는 실제 의미 정확성이나 성능 측정 결과가 아니다. 실제 진단과 Judge 호출은 사용자가 아래 절차로 별도로 실행한다.

## GT 검토

`evaluation/data/invite_cases.json`과 별도 `invite_gt.json`을 로컬 `policies/permissions.json`, `plans.json`, `troubleshooting.json`과 대조한다. member의 권한 부재, User COUNT(요청자 포함), 실효 member_limit, active·미만료의 AND 조건을 확인한다. C03/C05의 expired와 과거 만료일은 한 원인이다. 성공 결제로 구독을 활성화하거나 결제 실패를 추론하지 않는다. C04는 정상 조건이지만 실제 초대 성공/서비스 장애를 단정할 수 없어 needs_information이다. C05는 세 원인이 모두 필요하다. required_facts의 단순 생략은 오답이 아니고 모순 주장은 오답 근거가 될 수 있다. 자동 검사는 자연어 GT 전체의 타당성을 보증하지 않는다. 변경 시 버전을 올리고 manifest의 GT SHA-256/snapshot을 검토한 파일과 대조한다.

## 최초 1회: 환경 설정과 전용 DB 준비

Python 3.11+, uv, Docker Compose를 준비한다. 명령은 저장소 루트에서 한 줄씩 실행한다. 기존 `.env`는 유지하고, `.env.example`에 정의된 아래 항목을 로컬 편집기로 설정한다. `.env` 내용을 터미널에 출력하거나 공유하지 않는다. 기존 `.env`를 예제 파일로 덮어쓰지 않는다.

| 환경변수 | 설정 방법 |
|---|---|
| `POSTGRES_PASSWORD` | 기존 Compose PostgreSQL의 비밀번호를 유지한다. 기존 볼륨에서는 이 값 변경만으로 DB 비밀번호가 변경되지 않는다. |
| `POSTGRES_PORT` | 기존 호스트 포트를 유지한다. Compose 기본값과 예제는 `55432`다. |
| `DATABASE_URL` | 기존 개발 연결을 유지한다. 프로젝트 기본 DB 이름은 `support_dev`다. |
| `TEST_DATABASE_URL` | 기존 테스트 연결을 유지한다. 프로젝트 예제의 DB 이름은 `support_test`다. |
| `EVALUATION_DATABASE_URL` | 기존 `DATABASE_URL`의 드라이버·계정·비밀번호·호스트·포트를 유지하고 마지막 DB 이름만 `support_evaluation`으로 바꾼다. |
| `OPENAI_API_KEY` | 실제 실행에 사용할 키를 로컬에서 설정한다. |
| `OPENAI_MODEL` | Agent와 Workflow 양쪽에 사용할 진단 모델을 설정한다. |
| `EVALUATION_JUDGE_MODEL` | Calibration과 독립 채점에 사용할 Judge 모델을 설정한다. |

`compose.yaml`의 서비스명은 `postgres`, 접속 계정은 `support`, 개발 DB는 `support_dev`다. `.env.example`의 평가 DB 이름은 `support_evaluation`이다. 호스트에서 실행하는 Python의 URL은 `localhost`와 기존 `POSTGRES_PORT`를 사용한다. 비밀번호에 URI 특수문자가 있으면 URL에서 percent encoding한다. 개발 Bearer Token은 Evaluation에 필요하지 않다. 이미 같은 이름의 환경변수를 export했다면 `uv --env-file`보다 셸 값이 우선하므로 실행 전에 충돌을 해소한다.

먼저 의존성을 설치하고 기존 PostgreSQL을 기동한다. 기존 볼륨과 데이터는 유지한다.

```bash
uv sync --locked
docker compose --env-file .env up -d --wait postgres
```

평가 DB가 아직 없을 때만 다음 명령을 최초 1회 실행한다. 기존 `support` 계정을 소유자로 지정하므로 별도 사용자나 컨테이너를 추가할 필요가 없고, 평가용 schema를 생성할 수 있다. 이미 DB가 있으면 이 명령을 생략한다. `already exists` 오류가 나더라도 DB를 삭제하거나 재생성하지 않는다.

```bash
docker compose --env-file .env exec -T postgres createdb -U support -O support support_evaluation
```

Runner는 DB 자체를 생성하지 않는다. `DATABASE_URL`, `TEST_DATABASE_URL`은 이름 격리 검사에 필요하고 `EVALUATION_DATABASE_URL`은 실제 평가 연결이다. 평가 실행만 할 때는 테스트 DB가 실제로 존재할 필요는 없지만, URL은 설정해야 한다. 호스트 별칭이 달라도 개발·테스트와 같은 DB 이름은 거부한다. 실제 `current_database()`도 확인한다. 개발 DB 초기화·Seed·migration 명령은 평가 준비에 필요하지 않다.

각 run은 UUID로 만든 `eval_<uuid hex>` schema에 일곱 기존 ORM 테이블을 생성한다. 기존 schema를 재사용하지 않고 개발 Seed도 적용하지 않는다. Fixture insert는 하나의 트랜잭션이며 실패 시 rollback한다. 기존 데이터의 삭제/초기화와 자동 정리는 없다. 정상·실패 schema 모두 보존하며 manifest의 run_id/schema를 대조해 운영자가 별도로 수동 정리한다. schema 준비 중 실패해도 생성된 schema는 남을 수 있다. Fixture/정책/시간 조건이 바뀌면 후속 진단을 중단하고 해당 run을 invalid_environment로 취급한다. 실패 run을 재개하거나 같은 schema로 재시도하지 않는다.

## 기존 DB의 Trace migration

기존 API DB에서 새 코드를 사용하기 전에는 `migrations/002_llm_trace_metadata.sql`을 해당 DB에 수동 적용한다. 이 SQL은 `agent_traces.llm_metadata` nullable JSONB 열만 추가하며 backfill이나 기존 데이터 변경은 없다. 애플리케이션 시작 시 자동 실행하지 않는다. 새 테스트/Evaluation schema는 ORM 초기화로 해당 열을 생성하므로 별도 적용이 필요하지 않다. 과거 Pilot schema나 결과 파일을 이 작업으로 변경하지 않는다. 코드 rollback 시 추가 열은 그대로 두어 기록을 보존한다.

## 진단 Timeout 설정과 LLM 오류 확인

| 설정 | 우선순위 | 기본값 |
|---|---|---|
| 개별 LLM 요청 Timeout | `run --llm-request-timeout-seconds` → `LLM_REQUEST_TIMEOUT_SECONDS` | 20초 |
| Agent 실행 Budget | `run --execution-timeout-seconds` → `AGENT_TIMEOUT_SECONDS` | 60초 |
| Workflow 실행 Budget | `run --execution-timeout-seconds` → `WORKFLOW_TIMEOUT_SECONDS` | 60초 |

선택된 값은 양의 유한 숫자여야 한다. 명시 옵션은 같은 설정의 환경변수를 덮어쓰며 하위 값을 파싱하지 않는다. Evaluation에서 두 모드의 최종 Budget이 다르면 DB/Fixture/결과 디렉터리 생성 전에 `unequal_execution_budgets_use_--execution-timeout-seconds`로 중단한다. API의 모드별 Budget 설정은 기존처럼 독립적이다. 이 옵션들은 `run`의 진단에만 적용하고 `plan`·Judge·Calibration 설정은 바꾸지 않는다. 실제 진단 허용 옵션도 계속 필요하다.

개별 요청에는 설정 Timeout과 남은 실행 Budget 중 작은 값을 전달한다. SDK의 HTTP I/O Timeout과 전체 실행 Budget에 따른 취소는 별도다. Budget 소진 전 요청 Timeout/연결/HTTP 오류는 `llm_error`, 전체 Budget 소진은 `execution_timeout`이며 SDK 재시도는 0회다. DB 대기·종료 저장까지 포함한 전체 응답의 엄격한 wall-clock 상한을 뜻하지 않는다.

새 manifest의 `configuration.llm_request_timeout_seconds`, `agent_timeout_seconds`, `workflow_timeout_seconds`에는 실제 해석한 설정값을 기록한다. 각 `llm_response.llm_metadata.request_timeout_seconds`에는 해당 요청에 전달한 값을 남긴다. 예를 들어 설정 40초·잔여 Budget 6초이면 manifest는 40, 해당 Trace는 6이다. 성공·실패 모두 확인할 수 있다.

`llm_error`의 `llm_metadata.error`는 `exception_type`, `is_timeout`, `http_status`만 담는다. 예외 타입은 고정 허용 목록이며 미분류 예외는 `UnknownError`, HTTP Status 미확인은 null이다. `is_timeout=false`는 알려진 Timeout을 감지하지 않았다는 뜻이다. 예외 메시지·본문·Header·URL·스택·키는 기록하지 않는다. 정상 응답 및 다른 오류 코드의 `error`는 null이고 LLM 이외의 Trace는 `llm_metadata=null`이다.

결과 버전은 additive 확장으로 `evaluation-v1`을 유지한다. 새 Reader는 과거 결과를 읽을 수 있으나 누락 메타데이터/설정을 시간이나 새 기본값으로 추정하지 않는다. 원본 Pilot 파일은 수정하지 않는다. 새 Trace 필드를 모르는 이전 Reader는 새 결과를 거부할 수 있으므로 새 결과는 변경 이후 코드로 읽는다.

다음은 설정 예시이며 실제 호출을 허용할 때만 수동 실행한다. 기존 run 경로는 덮어쓰지 않고 새 경로를 사용한다.

```bash
uv run --env-file .env python -m scripts.evaluate run --repetitions 1 --llm-request-timeout-seconds 40 --execution-timeout-seconds 90 --output-dir .evaluation-results/invite-timeout-40 --allow-live-diagnosis
```

외부 호출 없이 추가 회귀 테스트를 실행하려면 `.venv/bin/python -m pytest tests/test_llm_reliability.py tests/test_evaluation_timeouts.py -q`를 사용한다. 저장/migration/CLI 통합 테스트는 기존 격리된 테스트 DB 환경변수를 준비한 뒤 `tests/test_llm_trace_storage.py tests/test_evaluation_timeout_integration.py`를 추가한다. 테스트는 Fake Client와 합성 예외를 사용한다.

## 원본 실행 기록 해석

run 생성 시 `manifest.json`, `executions.json`, `judgments.json`, `summary.json` 네 파일을 만든다. manifest는 코드 revision/dirty와 관련 코드·Prompt hash, Dataset/GT/정책 snapshot/hash, 기준 UTC, 모드 순서, 모델·한도, 가격표, DB schema를 남긴다. Settings 전체·접속 URL·키·Bearer는 직렬화하지 않는다. executions는 계획과 원본 Diagnosis/Trace 및 오류 영역을 보존한다. executions/manifest는 매 실행마다 임시 파일을 atomic replace하지만 네 파일 전체의 원자성을 보장하지는 않는다. 파일 오류/중단 때 이미 저장한 완료분과 미시작 계획은 유지되며 ID/버전 연결을 검사한다. 수집 완료 후 executions는 수정하지 않는다. 첫 채점은 미채점 judgments/summary만 확정하고 이후 변경은 별도 재채점 디렉터리에 저장한다.

Latency는 preflight·인증 후 문의 생성 직전부터 동기 diagnose 반환/예외까지 monotonic으로 측정한다. 생성·시작·종료 저장은 포함하고 사후 조회·Judge·파일 저장은 제외한다. HTTP 응답시간이나 Trace duration 합이 아니다. 기존 60초 Budget은 Service 전체의 엄격한 지연 상한이 아니다. `completed`는 needs_information/escalate와 의미 오답도 실행 성공이다. 처리된 `failed`는 다음 새 문의로 진행한다. 시작 후 running/종료 미확인은 `incomplete`, execution_success=false, censored=true다. 생성 실패는 `environment_error`, started=false, execution_success=null이며 실행 분모에서 제외한다. 결과 수집 오류는 원래 반환된 종료 판정을 덮어쓰지 않는다. DB 오류는 새 Session으로 저장 결과만 복구 조회하며 진단 재호출·자동 재개는 하지 않는다. 지속 장애나 Fixture 오류는 후속 실행을 중단하고 run 전체 비교를 무효화한다. 유실 Trace/저장되지 않은 invalid_output 원문은 복원할 수 없다.

## 일곱 Metrics와 누락 해석

Diagnosis Correctness는 correct/(correct+incorrect), Execution Success는 completed 성공 수/진단 Service 호출을 시작한 수다. review_required/pending은 정오답 분모에 넣지 않는다. needs_information/escalate도 유효한 completed면 실행 성공이다. E2E Latency, Input/Output/Total Tokens, LLM Calls, Tool Calls, Estimated Cost를 Case×Mode와 Mode 전체에서 집계한다. Token의 세 필드는 하나의 사용량 지표이며 공급자 total을 input+output으로 보충하지 않는다.

`all_started`는 처리된 실패를 포함하고 `successful_metrics`는 성공 실행만 포함한다. count/mean/median/min/max/missing_count, censored_count, observed_sum을 확인한다. 완전한 Trace의 llm_response/tool_result는 오류 시도도 1회다. usage 미제공·부분 usage·Trace 유실·알 수 없는 응답 모델은 null이며 0으로 바꾸지 않는다. 완전한 Trace에 LLM 호출이 없을 때만 Tokens/Cost/LLM Calls가 0이다. 부분 값은 observed에 보존하고 주 통계에는 완전한 값만 사용한다. 예를 들어 완료 10ms, 처리된 실패 30ms, Judge pending인 완료 20ms이면 전체 Latency 평균은 20ms이고 성공 평균은 15ms다. 종료 미확인 50ms는 censored로 별도 보존한다. 사전 준비 실패는 실행 분모에서 제외한다.

`evaluation/data/pricing.json`의 기본 models는 비어 있다. 실제 실행 전 사람이 모델별 응답 식별자, USD 입력/출력 per-million 단가(Decimal 문자열), pricing_version, as_of, source, assumptions를 확인해 채운다. 알 수 없는 가격은 비용 null이다. 실제 단가를 자동 확정하지 않는다. 호출별 `(input_tokens×input_rate + output_tokens×output_rate)/1,000,000`을 합해 소수 12자리 ROUND_HALF_EVEN으로 저장한다. 캐시/할인/계약/세금을 반영하지 않은 기본 단가 추정이며 실제 청구액이 아니다. Judge 호출·usage·Latency·Cost는 overhead에 따로 보존한다. 5개 Case·1회/5회 반복으로 보편적 우열이나 유의한 개선을 주장하지 않는다.

## Judge와 사람 Calibration

진단 수집을 마친 뒤 별도 명령으로 채점한다. Judge 입력은 GT의 expected_outcome/primary_causes/excluded_causes/required_facts와 Diagnosis의 summary/causes뿐이다. 모드/Case ID/실행 ID/다른 답변/Trace/비용/next_actions/evidence/실제 outcome은 보내지 않는다. 의미 판정과 별도로 Python에서 outcome_match를 검사한다. Diagnosis 없음은 not_applicable, Judge 통신·형식 오류는 judge_failed/pending이다. 기술 실패는 outcome 불일치가 있어도 pending으로 보존한다. 유효한 Judge 응답에서는 outcome 불일치가 최종 incorrect에 우선한다. missing_facts는 참고용이며 생략만으로 오답 처리하지 않는다. 필수 사실 모순을 오답 근거로 쓸 때는 reason에서 해당 주장과 GT를 대조한다.

Calibration v2의 입력은 `evaluation/data/judge_calibration_v2.json`이다. 기존 `judge_calibration.json`의 K01~K15 Diagnosis와 Human Label·개별 검토 기록을 그대로 보존하며, 현재 correct 8건/incorrect 7건이다. 최소 15건·C01~C05, 동의 표현·사실 생략/모순·원인 누락·추가 오답·C04 단정/추가 정보 요청·C05 복합 원인과 세 종류의 핵심 오답을 필수로 검토한다. 확정 correct/incorrect Label은 필수지만 review_required와 ambiguous 시나리오는 필수가 아니다. 유효한 review_required Human Label이 있으면 기존처럼 일치율 분모와 혼동표에 포함한다. AI/Fake 응답을 사람 판정으로 복사하지 않는다.

K16은 미확정인 `evaluation/data/judge_calibration_k16_draft.json`에 분리 보존하며 Calibration 입력에 포함하지 않는다. K17은 추가하지 않았다. 사용자는 K01~K15의 기존 Label과 K12의 보완된 판정 이유를 최종 승인하여 `examples_reviewed=true`, `examples_review_version=human-review-v2`를 기록했다. 실제 전체 검토자의 식별자는 제공되지 않아 `examples_reviewer=null`을 유지한다. 기존 pollux/rigel 기록으로 현재 검토자를 추정하지 않는다. 승인 여부와 검토자 식별은 별개이며, 실행 전 실제 검토자를 확인해 기록해야 Calibration 검토 메타데이터가 완성된다. 조건 변경이나 코드 테스트 통과를 사람 검토 완료로 기록하지 않는다. 개별 v1 검토 기록은 보존한다. 미완성 Label·검토 메타데이터가 있으면 잠정 결과는 보존하되 uncalibrated다.

Calibration 일치율은 전체 확정 Human Label 수를 분모로 하며 Judge 실패를 분모에서 빼지 않는다. 혼동표, 항목 원문·불일치·기술 실패, 핵심 오답의 false-correct 수와 버전을 확인한다. 대표 예제/Human Label 검토 완료, 기술 실패 0, 일치율 ≥90%, 지정 원인 누락·추가 오답·C04 단정 예제의 false-correct 0일 때만 calibrated다. 모델·실제 응답 모델·Prompt/Rubric/GT 버전이 바뀌면 적용하지 않는다. calibrated는 해당 Set·버전의 초기 검토 통과일 뿐 일반적 Judge 정확성이나 실제 평가 품질을 보장하지 않는다. 미완료/기준 미달 시 잠정 결과를 보존하되 품질 결론은 보류한다. review_required/pending, 사람 불일치, 핵심 오답, 사실 모순 reason을 우선 검토한다. Judge의 usage/Latency/추정비용은 judgments.items[].overhead에 진단 비용과 별도로 저장한다.

새 결과에는 `criteria_version=invitation-calibration-criteria-v2`와 `coverage.human_label_counts`(확정 Human Label별 사례 수), `coverage.unassessed_verdicts`(기준 사례 수 0인 유형), `limitation`을 기록한다. 이 정보는 채점 시 judgments/summary의 calibration에도 전달한다. 현재 15건에는 review_required 기준 사례가 없으므로 해당 유형의 의미 분류 성능은 검증되지 않았다. Judge가 review_required를 출력한 횟수는 Human Label의 검증 범위를 대신하지 않는다. 기준 사례가 있는 유형도 작은 Set에서의 비교일 뿐 일반적인 성능 보장이 아니다.

실제 Judge의 review_required는 기존처럼 별도 저장·집계하고 correct/(correct+incorrect)의 분모에서 제외한다. 사람이 해당 execution_id의 원본 Diagnosis와 GT, Judge reason을 검토한다. 별도 자동 승인·재분류 또는 새로운 Review UI를 추가하지 않는다. `quality_conclusion_ready=true`도 해당 Set·버전·표시된 검증 범위 내 검토 통과를 뜻하며 모든 판정 유형의 품질을 보장하지 않는다.

이전 Calibration 결과는 새 기준으로 재계산하거나 자동 승격하지 않는다. 이전 결과에 criteria_version/coverage가 없으면 적용 정보에 null과 미기록 한계를 표시하며 새 검증 범위를 추정하지 않는다. 기존 파일은 수정하지 않는다. 입력의 calibration_version, 검토의 examples_review_version, 통과 조건의 criteria_version은 서로 다른 버전이고, JSON 형식 schema_version은 additive 확장으로 calibration-v1을 유지한다.

## 매번 사용하는 평가 실행 명령

저장소 루트에서 `uv run --env-file .env`로 환경변수를 프로세스에 주입한다. PostgreSQL을 중지했다면 위 `docker compose ... up` 명령으로 다시 기동하고, DB 생성은 반복하지 않는다. `plan`과 테스트는 실제 LLM을 호출하지 않는다. `run --allow-live-diagnosis`는 진단 비용, `calibrate/judge --allow-live-judge`는 Judge 비용이 발생한다. 두 허용 옵션은 서로 대체되지 않는다. 옵션 없이 실행하면 외부 호출 전에 중단한다. 다음 실제 실행 명령들은 구현 검증 중 자동 실행하지 않았다.

```bash
uv run --env-file .env python -m scripts.evaluate --help
uv run --env-file .env python -m scripts.evaluate plan --repetitions 1
uv run --env-file .env python -m scripts.evaluate plan --repetitions 5
uv run --env-file .env python -m scripts.evaluate run --repetitions 1 --output-dir .evaluation-results/invite-10 --allow-live-diagnosis
uv run --env-file .env python -m scripts.evaluate calibrate --calibration-file evaluation/data/judge_calibration_v2.json --output .evaluation-results/calibration-v2.json --allow-live-judge
uv run --env-file .env python -m scripts.evaluate judge --run-dir .evaluation-results/invite-10 --calibration-result .evaluation-results/calibration-v2.json --allow-live-judge
uv run --env-file .env python -m scripts.evaluate run --repetitions 5 --output-dir .evaluation-results/invite-50 --allow-live-diagnosis
uv run --env-file .env python -m scripts.evaluate judge --run-dir .evaluation-results/invite-50 --calibration-result .evaluation-results/calibration-v2.json --allow-live-judge
uv run --env-file .env python -m scripts.evaluate judge --run-dir .evaluation-results/invite-10 --regrade --calibration-result .evaluation-results/calibration-v2.json --allow-live-judge
```

먼저 위 Human Review를 완료하고 별도 실제 호출 승인 후 Calibration을 실행한다. 결과의 status/reviewed, 일치율·기술 실패·critical_false_correct, 기준 버전·coverage·limitation을 확인한 뒤 독립 Judge 채점을 수행한다. 위 고정 출력 경로는 최초 실행 예시이며, 재수집에는 새 디렉터리, Calibration 재실행에는 새 파일 이름을 지정한다. 기존 calibration-v1.json이나 Pilot 결과를 덮어쓰지 않는다.

기존 Pilot v2 네 파일까지 보존하려면 다음처럼 첫 채점에도 `--regrade`를 사용한다. 새 채점 결과는 `invite-10-v2/regrades/<judgment_set_id>/`에 저장된다. 아래 명령은 실행 가이드이며 구현 검증 중 실행하지 않는다.

```bash
uv run --env-file .env python -m scripts.evaluate judge --run-dir .evaluation-results/invite-10-v2 --regrade --calibration-result .evaluation-results/calibration-v2.json --allow-live-judge
```

`--dataset`, `--gt`, `--pricing`으로 버전을 관리한 별도 JSON을 지정할 수 있다. GT 변경 후에는 `judge --regrade --gt <path>`를 사용하고 원본과 새 GT의 비교 제약을 확인한다. 재채점은 `regrades/<judgment_set_id>/`에 네 JSON과 source hash를 생성하며 진단 DB/API를 호출하지 않는다. executions의 원본 측정값을 유지하고 새 단가에 따른 파생 집계는 summary의 metrics_pricing_version으로 구분한다. 모델·단가·GT 변경을 기록하며 원본 judgments/summary를 덮어쓰지 않는다.

## DB 실패와 Secret 보호 범위

Evaluation CLI의 DB·실행 환경 실패는 오류 원문이나 traceback 대신 `평가 중단: <오류 코드>`를 출력하고 종료 코드 1을 반환한다. 준비 중 결과 디렉터리를 만든 뒤 실패하면 `manifest.json`의 `error_code`에도 안전한 코드만 기록한다.

| 오류 코드 | 구분 및 확인할 항목 |
|---|---|
| `invalid_database_selection` | URL 누락·파싱 실패·PostgreSQL 이외의 드라이버·개발/테스트 DB와 같은 이름 여부를 로컬에서 확인한다. |
| `database_unavailable` | 연결 또는 접속 DB 확인 실패. PostgreSQL 기동, DB 존재, 포트·계정·인증 설정을 확인한다. |
| `database_mismatch` | 실제 연결된 DB 이름이 예상과 다르다. |
| `schema_preparation_failed` | 평가 schema·테이블 준비 실패. schema 생성 권한 등을 확인한다. |
| `run_environment_error` | 엔진 구성 등 실행 환경 준비 중 예상하지 못한 오류다. |
| `evaluation_io_or_configuration_error` | CLI 최상위에서 처리한 나머지 설정·입출력 오류다. |

DB 드라이버와 SQLAlchemy 예외는 접속 인자·URL·Credential을 포함할 수 있으므로 원문을 출력하지 않는다. 합성 Credential을 넣은 URL 파싱·엔진 생성·연결·schema 실패 회귀 테스트로 stdout, stderr, 로그 및 결과 JSON을 확인한다. 오류 코드는 실패 단계를 구분하며 인증 실패나 연결 거부 같은 드라이버 상세 원인은 노출하지 않는다.

pytest의 DB fixture는 Evaluation CLI 경계를 거치지 않는다. 기존 테스트의 DB 준비 실패 traceback에 접속 설정이 포함된 문제는 이 경로에서 발생할 수 있다. 아래 `--tb=no`는 traceback 출력을 줄이는 보조 옵션이며 Secret 보호를 보장하지 않는다. 테스트 실패 출력·외부 DB 로그·별도 디버그 도구까지 CLI가 정제하지는 않으므로 공유 전에 확인하고, Secret 회귀 검증에는 실제 값 대신 합성 Credential만 사용한다.

## 자동 테스트

아래 테스트는 Fake/Mock과 격리된 TEST_DATABASE_URL만 사용한다. DATABASE_URL은 다른 이름인지 검사하는 용도이며 개발 DB에는 쓰지 않는다. 기존 테스트도 테스트 전용 schema를 사용한다. API Key가 필요하지 않고 실제 OpenAI 호출을 하지 않는다. DB 통합 테스트를 실행할 때 `support_test`가 아직 없다면 아래 준비 명령을 최초 1회만 실행하고, 이미 있으면 생략한다.

```bash
docker compose --env-file .env exec -T postgres createdb -U support -O support support_test
```

이후 테스트는 각 줄을 독립 실행한다.

```bash
uv run --env-file .env pytest tests/test_evaluation_dataset.py -q --tb=no
uv run --env-file .env pytest tests/test_evaluation_fixtures.py -q --tb=no
uv run --env-file .env pytest tests/test_evaluation_results.py -q --tb=no
uv run --env-file .env pytest tests/test_evaluation_runner.py -q --tb=no
uv run --env-file .env pytest tests/test_evaluation_metrics.py -q --tb=no
uv run --env-file .env pytest tests/test_evaluation_judge.py -q --tb=no
uv run --env-file .env pytest tests/test_evaluation_cli.py -q --tb=no
uv run --env-file .env pytest tests/test_evaluation_dataset.py tests/test_evaluation_fixtures.py tests/test_evaluation_runner.py tests/test_evaluation_judge.py tests/test_evaluation_metrics.py tests/test_evaluation_results.py tests/test_evaluation_cli.py -q --tb=no
uv run --env-file .env pytest -q --tb=no
```

Dataset/GT 형식·조건 대응, DB 격리/비파괴/commit, 원본 atomic 저장/ID 일치, 10/50건 순서와 Service 실행/실패 복구/측정 경계, 결측/Decimal 비용/분모, Judge 입력과 Calibration 조건, CLI 허용 옵션과 독립 재채점을 검증한다. Fake 통과는 의미 채점 품질의 측정이 아니다. 실제 가격/모델 설정·사람 Calibration·실제 10/50건 품질/효율 측정은 별도로 필요하다.

다음은 합성 상태 예시이며 실제 측정 결과가 아니다. completed와 pending은 서로 다른 책임의 상태이고, uncalibrated에서 품질 결론을 보류한다.

```json
{
  "execution": {
    "state": "completed",
    "started": true,
    "execution_success": true,
    "censored": false
  },
  "judgment": {
    "judge_status": "judge_failed",
    "semantic_verdict": null,
    "final_verdict": "pending",
    "outcome_match": true
  },
  "calibration": {"status": "uncalibrated"},
  "quality_conclusion_ready": false
}
```

실행 결과는 기본 `.evaluation-results/` 아래 저장되어 Git에서 제외된다. 사용자 지정 출력 경로는 합성 고객 원본/Trace를 포함하므로 커밋에 포함하지 않도록 직접 관리한다. 모델 응답의 원문 reasoning은 수집하지 않는다. 실제 실행 전 준비 사항은 전용 DB·환경변수, 모델별 가격표, GT 검토, 사람 Calibration label/검토 버전이며 실제 호출 허용 옵션은 사용자가 직접 지정한다.

## 구현 검증 기록 (2026-10-26)

새로 생성한 별도 PostgreSQL 16 테스트 컨테이너의 테스트 소유 schema와 Fake/Mock만 사용했다. 개발 DB와 기존 볼륨은 사용하거나 초기화하지 않았다. 신규 87개와 기존 338개를 포함한 전체 425개 테스트가 통과했다. 기존 Starlette/httpx 사용 중단 경고 1건이 남아 있다. 신규 테스트 수는 Dataset 8, Fixture 13, Results 6, Runner 20, Metrics 4, Judge 24, CLI 12다. Fake 10/50건 통합 경로와 독립 재채점/원본 보존을 검증했다. CLI help·plan 1/5회, 새 파일의 Ruff 형식/문법·import 검사, OpenSpec strict 검증도 통과했다.

실제 OpenAI 진단/Judge 호출, 실제 모델·가격 검증, 사람 Calibration label 확정, 실제 Evaluation 10/50건과 품질·효율 측정은 수행하지 않았다. 초기 기존 DB 연결 실패의 traceback에 접속 설정이 포함되어 이후 테스트 출력은 `--tb=no`로 제한했다. 이 옵션만으로 Secret 보호가 완료된 것은 아니며, CLI 경계와 pytest DB fixture의 보호 범위는 위 설명을 따른다.

Runbook/Secret 보완에서는 `.env`를 로딩하지 않고 합성 Credential과 Mock만 사용해 CLI 관련 14개 테스트가 통과했다(신규 DB 실패 5개 포함). `--tb=no` 없이 stdout·stderr·로그·결과 JSON의 Credential 비노출과 문서의 CLI 인자 구문을 검증했다. 기존 오류 변환이 검증을 통과해 제품 코드는 변경하지 않았다. DB 통합 테스트 3개와 전체 회귀 테스트는 이번 보완에서 재실행하지 않았다. 기존 Starlette/httpx 사용 중단 경고 1건이 남아 있다. 실제 DB 준비 명령과 OpenAI/Evaluation 실행은 수행하지 않았다.

## Calibration v2 구현 검증 기록 (2026-10-27)

`.env`를 로딩하거나 열람하지 않고 기존 API Key·DB 설정을 테스트 프로세스에서 제거한 뒤 Fake/Mock 검증을 수행했다. Judge·Metrics·CLI·Results·Dataset 관련 테스트는 81개 통과, DB가 필요한 기존 3개는 이 실행에서 제외했다. 다음 명령은 외부 호출 없이 재현할 수 있다.

```bash
env -u OPENAI_API_KEY -u DATABASE_URL -u TEST_DATABASE_URL -u EVALUATION_DATABASE_URL .venv/bin/python -m pytest tests/test_evaluation_judge.py tests/test_evaluation_metrics.py tests/test_evaluation_cli.py tests/test_evaluation_results.py tests/test_evaluation_dataset.py -q --tb=short -k 'not fake_full_collection_grading_and_independent_regrade and not regrade_duplicate_rejected_and_judge_failures_preserved'
```

전체 테스트도 같은 환경에서 `pytest -q --tb=line -ra`로 실행했다. 결과는 **228 passed, 27 failed, 272 errors**이며 전체 통과가 아니다. 272건은 격리된 테스트 DB 설정 부재에 따른 fixture 준비 오류다. 27건은 이 환경의 TCP 소켓 생성 금지로 발생한 deadline/smoke 실패와 그에 따른 미생성 실행 상태 참조 실패다. Docker 소켓 접근도 차단되어 별도 테스트 DB를 준비할 수 없었다. DB·localhost TCP가 허용되는 환경에서 전체 테스트를 다시 실행해야 한다. 기존 Starlette/httpx 사용 중단 경고 1건이 있다.

기존 Calibration/Pilot 결과와 v1 입력·GT·Judge 판정/Rubric/Prompt·Metrics·CLI의 불변, K01~K15 및 K16 내용 보존을 hash/구조 비교로 확인했다. Ruff 형식·기본문법/import 검사와 OpenSpec strict 검증을 수행했다. Fake 테스트의 합성 상태는 사람 판정이나 의미 분류 품질의 측정이 아니며 실제 OpenAI 호출·Calibration 실행·v2 전체 검토 완료 처리는 수행하지 않았다.
