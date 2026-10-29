# Tasks

1~8절은 초기 구현의 완료 기록으로 보존한다. 이후 승인된 Calibration v2 작업은 9절에서 구분한다. [Design](design.md)의 D1~D9와 [Spec](specs/diagnosis-evaluation/spec.md)을 구현 기준으로 삼으며 실제 OpenAI 호출·Calibration 실행과 기존 평가 결과 수정은 하지 않는다.

## 1. Dataset과 GT

- [x] 1.1 `evaluation/dataset.py`와 `evaluation/data/invite_cases.json`에 공통 문의와 C01~C05의 User 배열·한도·구독·상대 날짜·동일 성공 결제를 정의한다. `tests/test_evaluation_dataset.py`에서 5개 고유 Case, 2명/3명 또는 2명/2명 조건, 요청자 포함 인원, 상태/날짜 일치와 중립 이름을 검증한다.
- [x] 1.2 별도 `invite_gt.json`에 expected_outcome/primary_causes/excluded_causes/required_facts와 정책 근거를 정의하고 Case/GT ID 일치·C05 세 원인·C04 needs_information/정상 조건을 단위 테스트한다. 사람이 로컬 정책과 GT를 대조할 검토 항목을 `docs/evaluation_runbook.md`에 작성하고 GT 버전/hash 기록을 확인한다.
- [x] 1.3 Dataset/GT의 형식·정책 ID·조건별 원인 매핑 오류를 사전 거부하고 SHA-256/snapshot을 생성한다. 누락·중복·모순 GT와 잘못된 날짜의 테스트에서 Fixture 쓰기 및 LLM 호출이 0회임을 검증한다.

## 2. Evaluation DB와 Fixture

- [x] 2.1 `evaluation/fixtures.py`에 Evaluation/개발/테스트 DB 이름 격리와 실제 current_database 검사를 구현한다. `tests/test_evaluation_fixtures.py`에서 미설정·동일 이름/호스트 별칭·비 PostgreSQL·실제 DB 불일치를 쓰기 전에 거부하고 오류에 URL/Secret이 없음을 검증한다.
- [x] 2.2 실행별 새 schema/search_path와 기존 ORM 테이블 생성을 구현하고 run 전용 ID로 Customer Fixture를 한 트랜잭션에 insert한다. 전용 테스트 DB의 테스트 소유 schema에서 기존 sentinel 데이터 보존, 기존 schema 거부, 실패 rollback, run 간 격리와 별도 연결 commit 가시성을 검증한다. Evaluation 경로에 drop/delete/truncate와 개발 Seed 적용이 없음을 확인한다.
- [x] 2.3 기존 authenticate·고객 조회 Service로 역할·관계·User COUNT·한도·구독/결제 DTO를 검사하고 실행 전후 snapshot/정책 hash 검증을 구현한다. User 추가·다른 조직 조회·정책 변경·기준 시각 조건 변화 시 후속 모델 호출이 중단되는 Fake 테스트를 작성한다.
- [x] 2.4 Runbook에 전용 DB 수동 준비, 환경변수 이름, schema 보존·수동 정리 경계와 실패 시 처리 절차를 작성한다. `.env.example`에 실제 Credential 없이 Evaluation URL/Judge 모델의 설정 구조를 안내하고 전체 설정 객체를 출력하지 않음을 검토한다.

## 3. 결과 원본과 실행 계획

- [x] 3.1 `evaluation/results.py`에 manifest/executions/judgments/summary의 버전·ID·상태 모델과 초기 네 JSON 생성·실행별 atomic 저장을 구현한다. `tests/test_evaluation_results.py`에서 round-trip, 원본 Diagnosis/Trace 보존, 파일 오류/중단 시 완료분 보존과 Secret 제외를 검증한다.
- [x] 3.2 `evaluation/runner.py`에 1회/5회 순차 계획과 홀수/짝수 모드 교차를 구현한다. `tests/test_evaluation_runner.py`에서 정확히 10/50개 실행, 반복→Case→모드 순서, 모드별 새 문의 ID와 동일 Fixture 사용을 Fake로 검증한다.
- [x] 3.3 코드 revision/dirty 상태·관련 코드/Prompt hash, Dataset/GT/정책 snapshot, 설정 모델/실제 응답 모델, 가격표·실행 한도·Latency boundary를 manifest에 기록한다. 필수 provenance와 비밀 없는 allowlist 직렬화를 결과 테스트로 검증하고 Runbook에 결과 파일 해석을 추가한다.

## 4. 기존 Service 기반 실행과 실패 처리

- [x] 4.1 DB에서 인증된 요청자 context와 실행별 Session으로 기존 Support 생성·진단·결과/Trace 조회를 연결한다. Fake Responses로 두 모드의 completed 저장과 기존 Evidence/정책 참조 검사 재사용을 검증하고 GT/Case 정답 이름·다른 모드 결과가 진단 입력에 없음을 검사한다.
- [x] 4.2 문의 생성 직전부터 진단 반환/예외까지 monotonic E2E 계측을 구현한다. 고정 시계 테스트로 preflight/조회/Judge/파일 저장 제외, 저장 시간 포함, Trace duration 합과의 차이, 실패 지연 보존을 검증한다.
- [x] 4.3 completed/failed/nonterminal·시작 전 준비 실패·DB 실행 오류·결과 수집 오류를 구분하고 재호출 없는 복구 조회를 구현한다. needs_information/escalate 성공, 의미 오답 실행 성공, 처리된 실패 후 진행, running/Trace 유실의 incomplete/censored, Fixture 오류·지속 DB 장애·파일 실패 시 중단을 Fake/격리 DB 테스트로 검증한다.
- [x] 4.4 Runbook에 Service E2E 범위, 60초 Budget의 한계, 실행 시작 분모, 진단 재시도/자동 재개 미지원과 환경 오류 시 run 무효화를 설명한다. 문서의 상태 예제가 Runner 결과와 일치하는지 Fake 결과로 확인한다.

## 5. Metrics와 비용 계산

- [x] 5.1 `evaluation/metrics.py`에 실패 시도를 포함한 llm_response/tool_result 개수와 필드별 usage 합산을 구현한다. `tests/test_evaluation_metrics.py`에서 정상/실패·오류 Tool·0회 호출·부분/null usage·Trace 유실을 검증하고 observed/complete 값을 구분한다.
- [x] 5.2 `pricing.json`의 버전/기준일/출처/모델별 USD 단가 계약과 Decimal 비용 환산을 구현한다. 합성 단가 테스트로 다중 호출·응답 모델 매핑·알 수 없는 모델/가격·부분 사용량·Judge overhead 분리를 검증하며 실제 단가를 임의로 확정하지 않는다.
- [x] 5.3 Case×Mode와 Mode 전체에서 correct/(correct+incorrect), 성공/시작 실행, all_started/successful의 수치 통계를 구현한다. 성공·실패·판정 보류·환경 오류·결측·censored가 섞인 테스트로 분자/분모·count/mean/median/min/max·null·observed_sum을 확인한다.
- [x] 5.4 Runbook에 일곱 Metrics의 경계·분모, 캐시 미반영 추정비용과 실제 청구액 차이, 작은 표본 해석을 작성한다. 합성 결과 예제로 실패를 전체 통계에서 제외하지 않고 누락을 0으로 해석하지 않음을 확인한다.

## 6. Judge와 Calibration

- [x] 6.1 `evaluation/judge.py`에 버전이 있는 Rubric/Prompt·독립 Responses 어댑터·구조화된 의미 판정을 구현한다. Correctness는 실제 원인 식별 정확성으로 정의하고 primary_causes 모두 식별·잘못된 추가 원인 없음·동의 표현 허용을 적용한다. required_facts는 GT 맥락으로 사용하고 모든 사실의 명시를 강제하지 않으며 필수 사실에 반하는 주장은 incorrect 근거로 삼을 수 있게 한다. 기존 GT/Diagnosis 계약을 유지하고 Groundedness를 독립 Metric으로 추가하지 않는다. `tests/test_evaluation_judge.py`에서 GT+summary/causes 외의 모드/ID/Trace/비용/다른 답변/next_actions/evidence/실제 outcome이 입력에 없고 유한 timeout·재시도 0·Fake 주입이 적용됨을 검증한다.
- [x] 6.2 Python outcome_match와 final 판정 우선순위, reason/missing_causes/missing_facts/incorrect_causes 저장을 구현한다. missing_facts는 참고용으로 유지하고 값만으로 incorrect를 강제하지 않는다. Fake Judge로 동의 표현, 사실 생략과 사실에 반하는 주장 및 reason의 모순 기록, C05 누락, 부가 오답, C04 단정/정상 조건 미나열, review_required, outcome 불일치, judge_failed→pending, Diagnosis 없음→not_applicable의 상태 처리를 검증한다. Fake 통과를 실제 의미 정확성으로 보고하지 않는다.
- [x] 6.3 다섯 Case·세 label, 필수 사실의 생략/모순과 지정 핵심 오답을 포함하는 약 15개 대표 Calibration 예제 및 사람이 채울 label/reason/검토 버전 구조를 초기 Set으로 작성한다. Runbook에 사람의 사전 판정 절차를 안내하고 사람 판정 부재/미완료 시 uncalibrated 처리와 허위 사람 label 생성 방지를 테스트한다.
- [x] 6.4 Calibration 일치율의 분자/분모·혼동표·항목별 판정/불일치 원문·Judge 실패·버전 연결을 구현한다. 일치율 ≥90%는 초기 MVP 검토 목표로 명시한다. 합성 사람 label/Fake 판정으로 calibrated 표시 조건(대표 예제/Human Label 검토 완료·Judge 기술 실패 없음·검토 목표 충족·필수 원인 누락/잘못된 부가 원인/C04 근거 없는 원인 단정의 지정 예제에서 false-correct 0), 미완료/기준 미달 시 uncalibrated, 전체 확정 Human Label 분모와 Judge/Prompt/Rubric/GT 버전 변경 시 무효화를 검증한다.
- [x] 6.5 판정·Calibration provenance와 Judge 별도 사용량/지연/추정비용을 저장한다. Runbook에 진단 수집 후 독립 채점, Calibration 미완료/미달 시 잠정 결과 보존과 품질 결론 보류, 사람 검토 대상을 설명하고 JSON 예제로 확인한다. calibrated는 해당 Set과 버전에 한정된 검토 통과이며 일반적인 Judge 정확성이나 실제 평가 품질을 보장하지 않음을 명시한다.

## 7. CLI와 재채점

- [x] 7.1 `scripts/evaluate.py`에 plan/run/calibrate/judge 및 1회/5회 반복 옵션을 연결한다. 실제 진단/Judge의 별도 명시 옵션과 기본 외부 호출 0회를 CLI Fake 테스트로 검증하고 실제 API Key 없는 자동 테스트를 유지한다.
- [x] 7.2 기존 원본을 읽어 별도 regrades 디렉터리의 네 JSON을 생성하는 재채점과 source hash 연결을 구현한다. 원본 불변·ID/버전 검증·중복 출력 거부·Judge 실패 보존·진단/DB 호출 0회를 테스트한다.
- [x] 7.3 Runbook에 Design D9의 plan·10건·50건·Calibration·채점·재채점 명령, 비용 발생 옵션, 테스트 항목과 명령을 완성한다. 결과 디렉터리의 저장소 추적 제외를 설정하고 `--help`/Fake CLI 검증으로 문서의 인자·경로가 구현과 일치함을 확인한다. 실제 평가 명령은 자동 실행하지 않는다.

## 8. 통합 완료 확인

- [x] 8.1 Fake Agent/Workflow/Judge와 격리 PostgreSQL에서 10건 및 50건의 생성→진단→조회→채점→집계→재채점을 통합 검증한다. 새 문의 개수, 교차 순서, 동일 데이터/정책·모델 조건, 원본 보존과 GT 비노출, 실패/결측 경로를 포함하여 Spec Scenario와 대조한다.
- [x] 8.2 `uv run pytest`로 신규 및 기존 회귀 테스트를 실행하고 실제 외부 호출 없이 결과를 기록한다. 기존 API/Diagnosis/정책/Agent·Workflow 실행 구조가 변경되지 않았고 별도 migration·새 필수 의존성이 없음을 diff로 확인한다.
- [x] 8.3 구현 내용·실행한 테스트·미해결 문제를 한국어로 보고하고, 실제 가격/모델 설정과 사람 Calibration·실제 10/50건 평가는 미수행이면 명시한다. Fake 검증 완료와 실제 품질/효율 측정을 구분하여 보고한 것을 완료 조건으로 삼는다.

## 9. Calibration v2 후속 작업

- [x] 9.1 대표성의 필수 확정 Label을 correct/incorrect 포함 검사로 바꾸고 ambiguous 필수 조건만 제거한다. 최소 15건·다섯 Case·나머지 필수 시나리오·핵심 오답 조건을 유지한다. K01~K15의 Diagnosis/Human Label을 보존하고 K16을 미확정 별도 초안으로 분리하며 v2 전체 검토는 미완료로 유지한다.
- [x] 9.2 Calibration 기준 버전과 확정 Label별 사례 수·미검증 유형·한계를 결과 및 judgments/summary의 Calibration 정보에 기록한다. 과거 결과의 범위는 미기록으로 취급하고 상태를 자동 승격하거나 원본을 변경하지 않는다.
- [x] 9.3 Fake 회귀 테스트로 기존 15건 수용, 선택적 review_required 비교, 대표성·일치율·기술 실패·Critical False-Correct 방지, review_required 출력/집계/저장과 이전 결과 호환을 검증한다. 실제 의미 분류 품질이나 실제 Calibration 측정으로 주장하지 않는다.
- [x] 9.4 Runbook에 v2 범위·한계·데이터/초안 경계와 실행 전 Human Review 및 새 결과 경로를 안내한다. 이전 완료 기록과 v1/Pilot 결과는 보존한다.
- [x] 9.5 관련 회귀 및 전체 테스트를 실제 API 없이 실행하고 결과·환경 제약을 기록한다. Rubric/Prompt/GT/CLI·기존 15건·K16 초안·기존 평가 결과의 보존과 OpenSpec 정합성을 최종 확인한다.

## Workflow follow-up

- 사용자가 모델·단가표·전용 DB·사람 Calibration을 준비한 후 Runbook에 따라 실제 호출을 직접 실행한다. 실제 Evaluation과 사람 판정 완료는 자동 구현 체크리스트의 실행 항목이 아니다.
- 결과를 검토하고 완료 조건이 충족된 뒤 별도 요청으로 OpenSpec sync/archive를 수행한다.
