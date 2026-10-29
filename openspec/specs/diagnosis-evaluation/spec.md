# diagnosis-evaluation Specification

## Purpose

동일한 팀원 초대 실패 시나리오에서 기존 Agent와 Workflow를 재현 가능한 조건으로 실행하고, 의미적 진단 정확성과 실행 성공·자원 사용을 분리하여 비교한다. 특정 구조의 우수성을 전제하지 않고 Case별 Trade-off와 측정 한계를 검토할 수 있는 실행·채점 원본을 제공한다.

## Requirements

### Requirement: 초대 실패 Dataset과 Ground Truth
Dataset은 C01~C05를 JSON으로 정의하고 모든 Case에 동일한 고객 문의를 사용해야 한다(MUST). GT는 expected_outcome, primary_causes, excluded_causes, required_facts를 포함하고 실제 고객 조회 계약과 버전이 식별되는 로컬 정책으로 정당화해야 한다(MUST). GT와 Case의 정답을 드러내는 이름은 진단 모델 입력·Tool 관찰에 제공하지 않아야 한다(MUST).

#### Scenario: 다섯 Case의 원인과 outcome
- **WHEN** Dataset을 검토한다
- **THEN** 문의는 모두 "팀원을 초대하려고 하는데 계속 실패합니다. 무엇이 문제인지 확인해 주세요."이고 C01은 member·활성 미만료·인원 여유로 권한 부족, C02는 admin·활성 미만료·한도 도달로 인원 제한, C03은 admin·expired 및 과거 만료일·인원 여유로 구독 만료, C04는 admin·활성 미만료·인원 여유로 원인 불명, C05는 member·expired 및 과거 만료일·한도 도달로 세 원인을 모두 기대한다
- **THEN** C01/C02/C03/C05의 expected_outcome은 diagnosed, C04는 needs_information이며 C04의 primary_causes는 비어 있고 정상 권한·구독·인원 조건과 원인 확정 불가를 required_facts로 정의한다

#### Scenario: 관찰과 정책의 한계
- **WHEN** C04의 GT 또는 C03/C05의 결제 관련 원인을 검토한다
- **THEN** C04에 실제 초대 성공이나 서비스 장애를 단정하지 않으며, 모든 Case에서 동일한 성공 결제 이력을 제공하되 결제 성공만으로 구독을 활성화하거나 구독 만료의 배경을 결제 실패로 확정하지 않는다

### Requirement: Evaluation DB의 격리와 비파괴 준비
Fixture 쓰기 전에 전용 PostgreSQL Evaluation DB를 명시적으로 선택하고 개발·테스트 DB와 이름이 다름을 검증해야 한다(MUST). 실행별 새 schema에만 기존 테이블과 Fixture를 구성하며 Case별 조직을 분리해야 한다(MUST). DB 삭제, 전체 테이블 초기화, 기존 Seed 덮어쓰기 및 기존 schema 재사용을 자동 수행해서는 안 된다(MUST).

#### Scenario: 잘못된 DB 또는 기존 schema
- **WHEN** Evaluation URL이 없거나 PostgreSQL이 아니거나 개발/테스트 DB와 이름이 같거나 실제 접속 DB가 예상과 다르거나 실행 schema가 이미 존재한다
- **THEN** Fixture 쓰기와 LLM 호출 전에 안전한 환경 오류로 중단하며 URL·Credential을 출력하지 않는다

#### Scenario: 반복 평가의 데이터 보존
- **WHEN** 서로 다른 두 run을 준비한다
- **THEN** 각 run은 별도 schema를 사용하고 기존 개발·테스트·평가 데이터는 유지되며 정리는 자동 수행하지 않는다

### Requirement: Fixture 상태와 평가 조건 검증
평가마다 UTC reference_time을 정하고 구독 만료일을 상대 날짜로 구성해야 한다(MUST). member_count는 요청자를 포함한 User 레코드 수여야 한다(MUST). 실행 전과 각 실행 사이에 고객 조회 결과·관계·정책 버전·GT의 일관성을 검증하고 불일치는 환경 오류로 중단해야 한다(MUST). 두 모드에 동일 Fixture·모델·공통 설정을 적용해야 한다(MUST).

#### Scenario: 재현 가능한 고객 상태
- **WHEN** 같은 Dataset과 reference_time으로 Fixture를 생성한다
- **THEN** 역할·사용자 수·한도·구독 상태와 상대 만료일·결제 상태가 재현되고 C01/C03/C04는 2명/한도 3명, C02/C05는 2명/한도 2명이 조회된다

#### Scenario: 실행 중 데이터 또는 정책 변경
- **WHEN** User 추가, 상태/만료 조건 변화, 정책 파일 변경 또는 GT와 Fixture의 모순을 감지한다
- **THEN** 후속 진단을 호출하지 않고 환경 오류와 기존 결과를 보존하며 해당 run의 비교를 유효한 최종 결과로 보고하지 않는다

### Requirement: 기존 Support 계약을 통한 순차 비교
Runner는 기존 문의 생성·진단·조회·Trace 계약을 재사용하고 Agent/Workflow 내부 실행·Prompt를 변경하지 않아야 한다(MUST). 실행 단위는 Case × Mode × 반복이며 매번 새 Support Request를 생성해야 한다(MUST). 순차 실행으로 1회 반복 10건과 5회 반복 50건을 지원하고 홀수 반복은 Agent→Workflow, 짝수 반복은 Workflow→Agent여야 한다(MUST).

#### Scenario: 5회 실행 계획
- **WHEN** 5개 Case를 5회 반복한다
- **THEN** 반복별 C01~C05 순서에서 각 Case의 모드 쌍을 교차 순서로 실행하여 총 50개의 서로 다른 문의 ID를 만들며 같은 Case의 고객 데이터는 유지한다

#### Scenario: 실패 후 진행과 중복 방지
- **WHEN** 한 모드가 처리된 failed 상태로 종료된다
- **THEN** 해당 실행을 재시도하지 않고 같은 Case의 다음 새 문의를 진행하며 completed 문의 재호출을 새 측정으로 세지 않는다

### Requirement: Execution Success와 실패 영역의 구분
Execution Success는 기존 검증을 통과한 Diagnosis가 completed로 저장된 경우 true여야 하며 의미적 정답과 분리해야 한다(MUST). failed 또는 실행 시작 후 종료 미확인은 false로 기록해야 한다(MUST). Fixture/GT·Runner 환경 오류와 Judge 오류는 별도 영역으로 기록하고 진단 오답으로 변환해서는 안 된다(MUST).

#### Scenario: outcome과 실행 성공의 차이
- **WHEN** C04에 유효한 needs_information이 저장되거나 C01에 형식은 유효하지만 원인이 틀린 diagnosed가 저장된다
- **THEN** 두 실행 모두 Execution Success=true이며 의미 채점 결과는 별도로 정해진다

#### Scenario: DB 오류 또는 종료 미확인
- **WHEN** 진단 중 DB 오류나 중단이 발생한다
- **THEN** 실행 재호출 없이 저장된 문의·Trace를 수집할 수 있는 범위에서 확인하며 completed 확인 시 true, failed/running/시작 후 미확인은 false로 기록하고 후자는 incomplete로도 표시한다
- **THEN** 진단 시작 전 준비 실패는 Execution Success의 분모에 넣지 않고, 실행 후 결과 수집 실패는 수집 오류로 따로 남겨 원래 실행 판정을 덮어쓰지 않는다

### Requirement: 모드 비공개 의미 채점과 outcome 검사
Judge는 GT와 Diagnosis의 summary·causes만으로 독립 채점하고 모드·다른 실행 결과·Trace·효율 지표를 받지 않아야 한다(MUST). Diagnosis Correctness는 실제 원인 식별 정확성으로, primary_causes 모두 식별·잘못된 추가 원인 없음·자연어 동의 표현 허용을 기준으로 해야 한다(MUST). required_facts는 원인 의미와 평가 맥락을 위한 GT 정보이며 모든 내용의 명시를 강제해서는 안 된다(MUST). 사실 생략만으로 incorrect로 판정해서는 안 되며 필수 사실에 반하는 잘못된 주장은 incorrect로 판정할 수 있다(MAY). 기존 GT 필드와 Diagnosis 계약을 유지하고 Groundedness를 독립 지표로 추가해서는 안 된다(MUST). Python은 expected_outcome을 별도 검사해 불일치를 최종 incorrect로 반영해야 한다(MUST).

#### Scenario: 누락과 잘못된 부가 원인
- **WHEN** C05 진단이 두 원인만 식별하거나 C01 진단이 권한 부족과 함께 결제 실패를 확정한다
- **THEN** 각각 누락 원인 또는 잘못된 원인을 담은 incorrect 판정이 저장된다

#### Scenario: 원인 불명과 의미 불명확
- **WHEN** C04 진단이 정상 조건과 추가 정보 필요를 설명하거나, 장애를 근거 없이 확정하거나, 표현의 의미가 불명확하다
- **THEN** 각각 correct, incorrect, review_required로 구분하고 summary와 causes를 함께 검토하며, 현재 정보로 원인을 확정할 수 없어 추가 정보가 필요하다고 표현하면 정상 조건을 모두 나열하지 않아도 correct로 판정한다

#### Scenario: 필수 사실의 생략과 모순 구분
- **WHEN** 필수 원인을 동의 표현으로 모두 식별하고 잘못된 추가 원인 없이 required_facts 일부를 생략하거나, 필수 사실에 반하는 잘못된 주장을 한다
- **THEN** 생략만으로는 incorrect로 판정하지 않으며, 모순을 오답 근거로 삼으면 reason에 해당 주장과 GT의 모순을 기록한다

#### Scenario: outcome만 불일치
- **WHEN** 의미적 원인 설명은 맞지만 expected_outcome과 다르다
- **THEN** semantic_verdict는 보존하고 outcome_match=false 및 최종 incorrect를 기록하며 Diagnosis 출력 구조는 변경하지 않는다

### Requirement: Judge 결과 구조와 판정 보류
채점은 semantic_verdict, final_verdict, 이유, 누락 원인·사실, 잘못된 원인, outcome 검사 및 Judge 버전을 구조화해 저장해야 한다(MUST). missing_facts는 검토 참고용으로 유지하며 값이 있다는 이유만으로 incorrect를 강제해서는 안 된다(MUST). Judge 통신·형식 오류는 judge_failed와 최종 pending으로 구분해야 한다(MUST). Diagnosis가 없는 실행은 not_applicable이고 Judge를 호출하지 않아야 한다(MUST). 불명확한 의미는 review_required이며 기술 실패와 구분해야 한다(MUST).

#### Scenario: Judge만 실패한 경우
- **WHEN** 정상 진단 실행 후 Judge timeout 또는 잘못된 JSON이 발생한다
- **THEN** 실행 성공·Latency·Usage는 유지하고 채점만 pending으로 남기며 해당 건을 incorrect 수에 포함하지 않는다

### Requirement: 사람 기준 Calibration
사람이 사전 판정한 최소 15개 Calibration 예제는 C01~C05와 확정 correct/incorrect Label, 동의 표현·사실 생략/모순·원인 누락·추가 오답·C04 단정/추가 정보 요청·C05 복합 원인을 포함해야 한다(MUST). review_required Label은 선택적으로 비교하고 ambiguous 시나리오는 강제하지 않으며 기존 유효 사례는 보존해야 한다(MUST). 일치율 ≥90%는 초기 검토 목표일 뿐 일반적 정확성 보장이 아니어야 한다(MUST). 전체 확정 Label을 분모로 Judge 실패도 포함하여 일치율·혼동표·실패 수·항목별 판정/불일치 원문·버전을 보존해야 한다(MUST).

#### Scenario: 초기 Set의 검토 통과 표시
- **WHEN** 대표 예제와 Human Label 검토가 완료되고 Judge 기술 실패 없이 일치율 검토 목표를 충족하며 지정된 필수 원인 누락·잘못된 부가 원인·C04의 근거 없는 원인 단정 예제의 false-correct가 0이다
- **THEN** 해당 Set과 같은 Judge 모델·Prompt·Rubric·GT 버전에 한해 calibrated로 표시하고 일반적인 Judge 정확성이나 실제 평가 품질의 보장으로 해석하지 않는다

#### Scenario: 두 확정 Label과 미검증 유형
- **WHEN** K01~K15에 correct/incorrect만 있고 ambiguous 시나리오가 없지만 나머지 대표성·사람 검토·일치율·기술 실패·핵심 오답 조건을 충족한다
- **THEN** 두 항목의 부재만으로 탈락시키지 않으며 review_required 기준 사례 수 0과 해당 유형의 성능 미검증을 명시한다

#### Scenario: 선택적 review_required와 이전 결과
- **WHEN** 확정 review_required Human Label이 추가되거나 기준 버전·검증 범위가 없는 과거 결과를 적용한다
- **THEN** 전자는 일치율 분모와 혼동표에 포함하고 후자는 새 검증 범위를 추정하지 않으며 원본 상태와 파일을 보존한다

#### Scenario: Calibration 미완료 또는 미달
- **WHEN** 사람 label이 미확정이거나 Judge 기술 실패 또는 기준 미달이 발생한다
- **THEN** uncalibrated로 표시하고 실제 채점을 잠정 결과로 보존하되 품질 결론을 확정하지 않으며, 사람이 확정한 review_required label은 유효한 기준 답안으로 구분한다

### Requirement: Calibration 검증 범위의 명시
Calibration 기준 버전, 확정 Human Label별 기준 사례 수, 기준 사례가 없는 미검증 판정 유형과 한계를 Calibration 결과 및 새 채점 결과의 Calibration 정보에 기록해야 한다(MUST). Judge 출력 빈도로 Human Label의 대표성을 대신해서는 안 된다(MUST). 과거 결과의 상태를 새 기준으로 재계산하거나 자동 승격해서는 안 되며, 기준 버전·범위가 없으면 미기록으로 취급해야 한다(MUST). Rubric과 Judge의 세 판정값, 실제 review_required의 Human Review 대상 분리는 유지해야 한다(MUST).

#### Scenario: 검증 범위 전달과 이전 결과 보존
- **WHEN** review_required 기준 사례가 없는 Calibration을 새 채점에 적용하거나 검증 범위가 없는 이전 결과를 읽는다
- **THEN** 전자는 해당 유형의 성능 미검증을 judgments/summary에 전달하고 후자는 범위를 추정하거나 원본 상태·파일을 변경하지 않는다

### Requirement: Latency와 호출 수의 측정 경계
End-to-end Latency는 Runner에서 문의 생성 시작부터 동기 진단 반환/예외까지 monotonic 시계로 측정해야 한다(MUST). Fixture·GT 검증, 사후 조회, Judge, 파일 저장은 제외해야 한다(MUST). 완전한 Trace에서 llm_response와 tool_result의 개수를 실패 시도 포함 호출 수로 계산해야 한다(MUST). 단계별 duration_ms 합이나 DB 시각 차이로 End-to-end를 대체해서는 안 된다(MUST).

#### Scenario: 실패 및 종료 미확인 시간
- **WHEN** 정상 완료, 처리된 실패 또는 종료 미확인 실행을 측정한다
- **THEN** 정상과 처리된 실패는 관찰된 전체 지연을 보존하고 종료 미확인은 관찰 시간과 censored 표시를 보존하여 최종 완료 Latency 통계에 섞지 않는다

#### Scenario: Trace 수집 불완전
- **WHEN** Trace가 완전한 실행에 오류 Tool 1건과 오류 LLM 1건이 있거나 Trace가 유실된다
- **THEN** 전자는 각각 호출 1회로 계산하고 후자는 확인된 개수만 observed 값으로 보존하며 완전한 호출 수는 null로 둔다

### Requirement: Token Usage와 Estimated Cost
Token은 기존 LLM Trace의 input/output/total usage를 각각 합산해야 한다(MUST). 미제공·유실 값을 0으로 추정하지 않고 부분 합계와 완전성 여부를 보존해야 한다(MUST). 비용은 버전이 있는 모델별 입력/출력 단가로 계산하고 단가·사용량·모델 식별이 불충분하면 완전한 비용은 null이어야 한다(MUST). Judge 비용은 진단 비교 비용에서 분리해야 한다(MUST).

#### Scenario: 부분 사용량과 알 수 없는 모델
- **WHEN** 두 LLM 호출 중 하나의 usage가 없거나 가격표에 응답 모델이 없다
- **THEN** 알려진 값과 누락 이유를 남기고 전체 Token/Cost를 과소한 확정값으로 표시하지 않는다

#### Scenario: 가격과 청구액의 구분
- **WHEN** 사용량과 가격표가 모두 있다
- **THEN** 호출별 (input_tokens × 입력 단가 + output_tokens × 출력 단가) / 1,000,000을 합산하고 USD 단가 버전·기준일을 기록하며 캐시 할인 등을 반영하지 않은 추정임을 명시한다

### Requirement: 분모와 결측을 보존하는 집계
집계는 Case별·Mode별 및 Mode 전체에서 정답/오답/검토/보류/실행 실패·환경 오류를 분리하고 분모를 명시해야 한다(MUST). Correctness는 correct/(correct+incorrect), Execution Success는 성공/시작된 실행 수여야 한다(MUST). Latency·사용량·호출·비용은 성공 실행과 전체 시작 실행을 따로 집계하며 결측·부분 값은 완전한 값과 섞지 않아야 한다(MUST).

#### Scenario: 작은 표본과 실패 포함 통계
- **WHEN** 성공 1건, 처리된 실패 1건, Judge 보류 1건, 준비 오류 1건을 집계한다
- **THEN** 준비 오류는 실행 분모에서 제외되고 Judge 보류는 정오답 분모에서 제외되며, 실패 실행의 확인된 지연·사용량은 전체 실행 통계에 포함된다
- **THEN** 모든 비율에 분자/분모, 모든 수치 통계에 유효 표본 수·누락 수가 있고 표본이 없으면 null이며 1회/5회 반복으로 일반적 우열이나 유의한 개선을 주장하지 않는다

### Requirement: JSON 원본 보존과 독립 재채점
결과는 manifest.json, executions.json, judgments.json, summary.json으로 저장해야 한다(MUST). 버전·run/실행 식별자·실행 순서·reference_time·설정·정책/Dataset/GT·원본 Diagnosis/Trace·오류·측정 완전성을 보존해야 한다(MUST). 각 실행 후 결과를 보존하고 재채점은 원본 실행을 변경하거나 진단을 재호출하지 않아야 한다(MUST).

#### Scenario: 중단 이후 재채점
- **WHEN** 실행 중단 또는 Judge 변경 후 기존 결과를 재채점한다
- **THEN** 실행 완료분과 미시작 계획이 구분되고 기존 judgments/summary는 덮어쓰지 않으며 새 채점 식별자와 원본 연결로 Judge만 다시 실행한다

### Requirement: 자동 테스트와 실제 실행의 분리
자동 테스트는 Fake/Mock LLM·Judge로 수행하고 실제 API Key를 요구하거나 외부 LLM을 호출해서는 안 된다(MUST). 한국어 docs Runbook은 테스트 항목·명령과 전용 DB 준비·10회/50회 실행·Calibration·재채점 절차를 안내해야 한다(MUST). 실제 진단과 Judge에는 각각 명시적 실행 옵션이 필요하며 Secret·접속 문자열을 결과나 로그에 저장해서는 안 된다(MUST).

#### Scenario: 기본 명령과 실제 실행 옵션
- **WHEN** 실제 호출 옵션 없이 실행하거나 자동 테스트를 수행한다
- **THEN** 실제 진단·Judge 호출 수는 0이며, Runbook은 실제 호출 옵션의 비용 발생 범위와 환경변수 이름만 안내한다
