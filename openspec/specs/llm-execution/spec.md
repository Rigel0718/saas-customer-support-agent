# llm-execution Specification

## Purpose

Agent와 Workflow의 공통 LLM 실행에 안전한 실패 분류와 설정 가능한 요청 Timeout을 제공한다. 진단 전략과 실행 결과 계약을 유지하면서 평가에 실제 적용한 설정과 호출별 제약을 확인할 수 있게 한다.

## Requirements

### Requirement: 안전한 LLM 오류 분류
공통 LLM 호출 중 `llm_error`가 발생하면 허용 목록으로 정규화한 예외 타입, 감지된 Timeout 여부 및 확인 가능한 HTTP Status를 해당 호출의 Trace 메타데이터에 기록해야 한다(MUST). 분류할 수 없는 예외는 `UnknownError`, 알 수 없는 HTTP Status는 null로 기록해야 한다(MUST). 오류 분류는 기존 최종 오류 코드나 실행 상태를 변경해서는 안 된다(MUST).

#### Scenario: 요청 Timeout과 연결 오류 구분
- **WHEN** 실행 Budget이 남아 있는 상태에서 각각 알려진 요청 Timeout 예외와 연결 오류 예외가 발생한다
- **THEN** 둘 다 `llm_error`이며 예외 타입을 구분하고 Timeout 여부는 각각 true와 false, HTTP Status는 null로 기록한다

#### Scenario: HTTP 오류
- **WHEN** 공급자의 HTTP 429 또는 500 오류가 발생한다
- **THEN** `llm_error`와 정규화한 예외 타입 및 실제 정수 HTTP Status를 기록하고 Timeout으로 표시하지 않는다

#### Scenario: 미분류 예외
- **WHEN** 허용 목록에 속하지 않는 예외가 발생한다
- **THEN** 예외 클래스 이름을 동적으로 출력하지 않고 `UnknownError`, 감지된 Timeout=false, HTTP Status=null로 기록한다

### Requirement: 오류 진단 정보의 수집 제한
새 LLM 메타데이터는 허용된 분류 문자열·불리언·숫자·null만으로 구성해야 한다(MUST). 예외 메시지, args, repr, 스택, 예외 원인 체인의 원문, 요청/응답 본문·URL·Header, API Key·인증 토큰·접속 정보는 메타데이터·오류 로그·오류 응답에 기록해서는 안 된다(MUST). 기존 Tool 관찰 및 Diagnosis 저장 계약은 유지해야 한다(MUST).

#### Scenario: 예외에 민감정보 포함
- **WHEN** Fake 예외의 메시지·본문·Header·URL 또는 사용자 정의 클래스 이름에 민감정보 표식이 포함된다
- **THEN** 새 메타데이터와 오류 로그·오류 응답에 표식이 없고 허용된 분류 정보만 남는다

### Requirement: 요청 Timeout 설정과 실행 Budget 연동
Agent와 Workflow는 동일한 요청 Timeout 설정 `LLM_REQUEST_TIMEOUT_SECONDS`를 사용하고 기본값은 20초여야 한다(MUST). 값은 양의 유한 초 단위 숫자여야 하며 잘못된 설정은 실행 전에 거부해야 한다(MUST). 각 호출에는 설정값과 남은 실행 Budget 중 작은 값을 적용하고, 전체 Budget에 따른 진행 중 I/O 취소를 유지해야 한다(MUST).

#### Scenario: 기본값과 명시 설정
- **WHEN** 요청 Timeout을 생략하거나 35초로 지정하고 남은 Budget이 50초다
- **THEN** 각각 20초와 35초가 실제 요청 Timeout으로 적용된다

#### Scenario: 남은 Budget이 더 짧음
- **WHEN** 요청 Timeout은 35초이고 남은 실행 Budget은 7초다
- **THEN** 실제 요청 Timeout은 7초이며 전체 Budget의 취소 경계도 유지된다

#### Scenario: 유효하지 않은 설정
- **WHEN** 요청 Timeout 또는 명시 실행 Budget이 0, 음수, NaN, 무한대 또는 숫자가 아닌 값이다
- **THEN** 값을 출력하지 않는 안전한 설정 오류로 거부하고 진단 LLM을 호출하지 않는다

### Requirement: 기존 실패 및 진단 전략 유지
실행 Budget 소진은 `execution_timeout`, Budget 소진 전 요청 Timeout·통신 오류는 `llm_error`로 유지해야 한다(MUST). SDK 재시도는 0회여야 하며 Prompt, Tool 선택 전략·한도, Workflow 조회 순서·단일 LLM 호출, `needs_information` 판단 및 Diagnosis 검증을 변경해서는 안 된다(MUST).

#### Scenario: 전체 Budget 소진
- **WHEN** 진행 중 LLM I/O에서 전체 실행 Budget이 소진된다
- **THEN** I/O를 취소하고 `execution_timeout`으로 끝나며 추가 LLM·Tool 호출과 재시도가 없다

#### Scenario: 거부 및 불완전 응답
- **WHEN** 모델이 거부하거나 불완전 응답을 반환한다
- **THEN** 각각 기존 `model_refusal`과 `invalid_output`을 유지하고 새 오류 분류를 이유로 재호출하지 않는다

### Requirement: Evaluation Timeout 해석과 동등 조건
Evaluation `run`은 `--llm-request-timeout-seconds`와 두 모드 공통의 `--execution-timeout-seconds`를 지원해야 한다(MUST). 요청 Timeout은 CLI 옵션, `LLM_REQUEST_TIMEOUT_SECONDS`, 기본 20초 순으로 해석해야 한다(MUST). 공통 Budget 옵션이 없으면 기존 두 모드 Budget 환경변수를 각 기본 60초로 해석해야 하며, 최종 Budget이 다르면 평가 시작 전에 거부해야 한다(MUST).

#### Scenario: 명시 옵션 우선
- **WHEN** 환경변수에 요청 20초·Agent 60초·Workflow 90초가 있고 CLI에 요청 40초·공통 Budget 120초를 지정한다
- **THEN** 요청 40초와 두 모드 Budget 120초를 동일 설정 객체로 실제 실행에 전달한다

#### Scenario: 환경변수 반영
- **WHEN** CLI 옵션 없이 요청 30초·Agent와 Workflow Budget 각각 80초를 환경변수로 설정한다
- **THEN** 요청 30초와 두 모드 Budget 80초가 실제 실행에 반영된다

#### Scenario: 모드 간 불일치
- **WHEN** 공통 Budget 옵션 없이 Agent 60초·Workflow 90초로 해석된다
- **THEN** DB 연결·Fixture 생성·결과 디렉터리 생성·LLM 호출 전에 안전한 설정 오류로 중단한다

### Requirement: Evaluation의 실제 설정 보존과 이전 결과 호환
새 평가 manifest에는 실제 실행에 전달한 요청 Timeout과 두 모드 실행 Budget을 기록하고, 각 LLM 호출 Trace에는 실제 적용 요청 Timeout을 기록해야 한다(MUST). 이전 결과의 누락 메타데이터는 알 수 없음으로 취급하고 실행 시간으로 추정하거나 새 기본값으로 보충해서는 안 된다(MUST). 기존 Pilot 파일을 수정해서는 안 된다(MUST).

#### Scenario: 설정값과 호출값 차이
- **WHEN** manifest 요청 Timeout이 40초이고 어느 호출의 남은 Budget이 6초다
- **THEN** manifest에는 설정 40초, 해당 Trace에는 적용 6초가 보존되고 실패 시에도 확인할 수 있다

#### Scenario: 이전 결과 조회 및 재채점 준비
- **WHEN** 새 필드가 없는 Pilot v1 결과를 읽거나 재채점 입력으로 준비한다
- **THEN** 기존 내용과 지표를 읽을 수 있고 누락 메타데이터는 null 또는 미기록 상태이며, 원본 파일의 byte와 hash는 유지된다

### Requirement: 실제 API 없는 회귀 검증
안전한 오류 분류, 설정 우선순위, Budget 연동과 저장·결과 호환은 Fake Client·합성 예외·고정 시계를 사용해 검증할 수 있어야 한다(MUST). 자동 테스트에는 실제 OpenAI API Key나 외부 LLM 호출이 필요해서는 안 된다(MUST). Fake 검증을 실제 진단 정확도 또는 안정성 개선 측정으로 보고해서는 안 된다(MUST).

#### Scenario: 두 모드 회귀
- **WHEN** 같은 고정 응답과 안전한 합성 실패로 Agent·Workflow를 검증한다
- **THEN** 실제 외부 호출 없이 설정값·오류 분류·기존 실행 상태와 진단 계약이 검증되고 성공·실패마다 LLM 시도당 Trace는 한 건이다
