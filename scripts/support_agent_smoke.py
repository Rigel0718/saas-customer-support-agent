"""User-run live smoke test: python -m scripts.support_agent_smoke."""

import getpass
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from sqlalchemy.engine import make_url

from app.config import Settings
from app.diagnosis.runtime import ExecutionFailure
from app.diagnosis.tools import ARGUMENTS
from app.schemas.support import RequestDTO, TracesDTO
from app.services.support import validate_diagnosis

ROOT = Path(__file__).resolve().parents[1]
MEMBER = "00000000-0000-0000-0000-000000000103"
ORG = "00000000-0000-0000-0000-000000000001"
CONTENT = (
    "결제했는데 팀원을 초대할 수 없습니다. 제 계정의 초대 권한과 조직 팀원 한도, "
    "구독 및 결제 상태를 확인하고 관련 정책을 근거로 원인이나 추가 확인할 사항을 안내해 주세요."
)
LABELS = [
    "Responses API 연결", "Tool Calling·후속 선택", "진단 생성·저장·근거",
    "AgentTrace 기록", "종료 상태·Diagnosis 일관성",
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SmokeError(Exception):
    """Only fixed, safe messages may be passed to this exception."""


def assess(post: dict | None, retrieved: dict | None, traces: dict | None) -> list:
    results = [["미검증", "후속 응답을 수집하지 못함"] for _ in LABELS]
    if traces is None:
        return results
    try:
        items = TracesDTO.model_validate(traces).items
    except ValueError:
        return [["Fail", "Trace 응답 형식 오류"] for _ in LABELS]
    llms = [t for t in items if t.event_type == "llm_response"]
    tools = [t for t in items if t.event_type == "tool_result"]
    connected = any(not t.error_code and t.model and t.response_id for t in llms)
    results[0] = ["Pass" if connected else "Fail", f"LLM 기록 {len(llms)}건"]
    successful = [t for t in tools if t.observation and t.observation.get("ok")]
    customer = {"get_user", "get_organization", "get_subscription", "get_payment_history"}
    tool_ok = (
        any(t.tool_name in customer for t in successful)
        and any(t.tool_name == "search_policy" for t in successful)
        and all(
            t.call_id and t.arguments is not None
            and t.observation.get("trace_sequence") == t.sequence
            for t in tools if t.observation
        )
        and all(t.observation is not None for t in tools)
    )
    for t in successful:
        data = t.observation.get("data") or {}
        try:
            ARGUMENTS[t.tool_name].model_validate(t.arguments)
        except (KeyError, ValueError):
            tool_ok = False
        if t.tool_name in customer:
            key = "user_id" if t.tool_name == "get_user" else "organization_id"
            expected = MEMBER if key == "user_id" else ORG
            tool_ok = tool_ok and (t.arguments or {}).get(key) == expected
        if t.tool_name == "get_user":
            tool_ok = (
                tool_ok and data.get("role") == "member"
                and data.get("organization_id") == ORG
            )
    followup = any(
        first.step is not None and later.step is not None and later.step > first.step
        and any(l.step == later.step and l.sequence < later.sequence for l in llms)
        for first in successful for later in tools
    )
    results[1] = [
        "Fail" if not tool_ok else "Pass" if followup else "미검증",
        f"Tool sequence: {[t.sequence for t in tools]}; 후속 Tool 선택: {followup}",
    ]
    completed = False
    if retrieved is not None:
        try:
            dto = RequestDTO.model_validate(retrieved)
            completed = (
                dto.status == "completed" and dto.error_code is None
                and dto.diagnosis is not None
            )
            if completed:
                validate_diagnosis(json.dumps(retrieved["diagnosis"]), items)
                results[2] = ["미검증", "생성·저장·근거 참조 통과; 설명과 관찰의 의미 일치는 사람 검토 필요"]
            else:
                results[2] = ["Fail", "정상 진단이 저장되지 않음"]
        except (ValueError, ExecutionFailure):
            completed = False
            results[2] = ["Fail", "진단 형식 또는 근거 참조 오류"]
    events = [t.event_type for t in items]
    trace_ok = (
        bool(items) and [t.sequence for t in items] == list(range(1, len(items) + 1))
        and events[0] == "execution_started"
        and events[-2:] == ["final_result", "execution_completed"]
        and {"llm_response", "tool_result"}.issubset(events)
        and all(t.duration_ms is not None and t.duration_ms >= 0 for t in llms + tools)
        and all(t.model and t.response_id for t in llms if not t.error_code)
    )
    results[3] = ["Pass" if trace_ok else "Fail", f"{len(items)}건; 종료: {events[-2:]}"]
    if post is not None and retrieved is not None:
        dates_ok = False
        try:
            dates = [datetime.fromisoformat(retrieved[k].replace("Z", "+00:00"))
                     for k in ("created_at", "started_at", "finished_at")]
            dates_ok = dates[0] <= dates[1] <= dates[2]
        except (KeyError, ValueError, TypeError, AttributeError):
            pass
        same = post == retrieved
        results[4] = [
            "Pass" if same and completed and dates_ok and trace_ok else "Fail",
            f"POST/GET 일치: {same}; 시간 순서: {dates_ok}",
        ]
    return results


class Runner:
    def __init__(self, directory: Path):
        self.directory = directory
        self.meta = {"시작": now(), "수행자": getpass.getuser()}
        self.responses = {}
        self.codes = {}
        self.failure = None
        self.stage = "설정 확인"
        self.hidden = []
        self.process = None
        self.token = secrets.token_urlsafe(32)
        self.hidden.append(self.token)
        self.opener = build_opener(ProxyHandler({}))

    def clean(self, text: str) -> str:
        for value in sorted(set(self.hidden), key=len, reverse=True):
            if value:
                text = text.replace(value, "[REDACTED]")
        return text

    def write(self, name: str, value) -> None:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        path = self.directory / name
        path.write_text(self.clean(text), encoding="utf-8")
        path.chmod(0o600)

    def command(self, args: list[str], timeout: float = 120) -> None:
        result = subprocess.run(
            args, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=timeout, check=False,
        )
        if result.returncode:
            raise SmokeError("명령 실패: " + " ".join(args[:4]) + " (원본 오류는 Secret 보호를 위해 저장하지 않음)")

    def http(self, name: str, method: str, path: str, body=None, timeout=15):
        request = Request(
            self.base_url + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
        )
        try:
            response = self.opener.open(request, timeout=timeout)
        except HTTPError as exc:
            response = exc
        with response:
            self.codes[name] = response.status
            try:
                data = json.load(response)
            except (ValueError, UnicodeError):
                raise SmokeError("HTTP 응답이 JSON 형식이 아님") from None
        self.responses[name] = data
        self.write(name + ".json", data)
        return data

    def require(self, name: str, status: int) -> dict:
        if self.codes.get(name) != status:
            raise SmokeError(f"{name}: 기대 HTTP {status}, 실제 {self.codes.get(name)}")
        return self.responses[name]

    def run(self):
        self.settings = Settings.from_env()
        settings = self.settings
        self.hidden.extend(settings.bearer_tokens)
        self.hidden.extend(filter(None, [settings.openai_api_key, settings.database_url,
                                        make_url(settings.database_url).password,
                                        os.environ.get("POSTGRES_PASSWORD")]))
        if not settings.openai_api_key or not settings.openai_model:
            raise SmokeError(".env에 OPENAI_API_KEY와 OPENAI_MODEL 설정이 필요함")
        self.meta.update({
            "모델": settings.openai_model,
            "한도": (
                f"{settings.agent_max_steps} / {settings.agent_max_tool_calls} / "
                f"{settings.agent_timeout_seconds}"
            ),
        })
        revision = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                  capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout
        self.meta["코드"] = f"{revision} / 로컬 변경 {'있음' if dirty else '없음'}"
        self.stage = "PostgreSQL 기동"
        print(self.stage, flush=True)
        self.command(["docker", "compose", "up", "-d", "--wait", "postgres"])
        self.stage = "테이블 생성 및 Seed 갱신"
        print(self.stage, flush=True)
        self.command([sys.executable, "-m", "app.db"])
        self.meta["Seed 시작"] = now()
        self.command([sys.executable, "-m", "app.seed"])
        self.meta["Seed 종료"] = now()
        self.start_backend()
        self.stage = "고객 데이터 및 문의 생성 확인"
        for name, path in {
            "user": f"/users/{MEMBER}", "organization": f"/organizations/{ORG}",
            "subscription": f"/organizations/{ORG}/subscription",
            "payments": f"/organizations/{ORG}/payments",
        }.items():
            self.http(name, "GET", path)
            self.require(name, 200)
        if (
            self.responses["user"]["role"] != "member"
            or self.responses["user"]["organization_id"] != ORG
        ):
            raise SmokeError("시나리오 사용자·조직 불일치")
        created = self.http("created", "POST", "/support/requests", {"content": CONTENT})
        self.require("created", 201)
        dto = RequestDTO.model_validate(created)
        if (
            dto.status != "pending" or str(dto.requester_user_id) != MEMBER
            or str(dto.organization_id) != ORG or dto.content != CONTENT
            or any(
                created[k] is not None
                for k in ("started_at", "finished_at", "diagnosis", "error_code")
            )
        ):
            raise SmokeError("문의 생성 응답 불일치")
        self.path = f"/support/requests/{dto.id}"
        before = self.http("before", "GET", self.path)
        self.require("before", 200)
        before_traces = self.http("before-traces", "GET", self.path + "/traces")
        self.require("before-traces", 200)
        if before != created or before_traces != {"items": []}:
            raise SmokeError("진단 전 문의·Trace 불일치")
        self.stage = "실제 진단 1회 실행"
        print(self.stage + " (모델 호출 비용 발생 가능)", flush=True)
        self.meta["진단 요청 UTC"] = now()
        try:
            self.http("diagnosed", "POST", self.path + "/diagnose",
                      timeout=settings.agent_timeout_seconds + 90)
        finally:
            # Never retry the POST, including HTTP errors and timeouts.
            self.meta["진단 응답 UTC"] = now()
            for name, suffix in (("retrieved", ""), ("traces", "/traces")):
                try:
                    self.http(name, "GET", self.path + suffix)
                    self.require(name, 200)
                except (SmokeError, URLError, TimeoutError):
                    self.responses.pop(name, None)
        self.require("diagnosed", 200)
        self.stage = "결과 판정"

    def start_backend(self):
        self.stage = "임시 Backend 시작"
        env = os.environ.copy()
        tokens = {k: str(v) for k, v in self.settings.bearer_tokens.items()}
        tokens[self.token] = MEMBER
        env["DEV_BEARER_TOKENS"] = json.dumps(tokens)
        # Reserve the listening socket and pass it to our own server: no port race.
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            self.base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            self.process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
                 "--fd", str(listener.fileno()), "--log-level", "critical", "--no-access-log"],
                cwd=ROOT, env=env, pass_fds=(listener.fileno(),),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            for _ in range(100):
                if self.process.poll() is not None:
                    raise SmokeError("Backend 시작 실패")
                try:
                    with self.opener.open(self.base_url + "/openapi.json", timeout=0.2):
                        break
                except (URLError, TimeoutError):
                    time.sleep(0.1)
            else:
                raise SmokeError("Backend 시작 대기 시간 초과")

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

    def report(self) -> int:
        self.meta["종료"] = now()
        post = self.responses.get("diagnosed") if self.codes.get("diagnosed") == 200 else None
        retrieved = self.responses.get("retrieved")
        traces = self.responses.get("traces")
        results = assess(post, retrieved, traces)
        final = retrieved or post or self.responses.get("created", {})
        if not isinstance(final, dict):
            final = {}
        diagnosis = final.get("diagnosis") or {}
        try:
            items = TracesDTO.model_validate(traces).model_dump(mode="json")["items"]
        except ValueError:
            items = []
        llms = [t for t in items if t.get("event_type") == "llm_response"]
        tools = [t for t in items if t.get("event_type") == "tool_result"]
        usage = {key: sum(t["usage"].get(key) or 0 for t in llms if t.get("usage"))
                 for key in ("input_tokens", "output_tokens", "total_tokens")}
        provided = sum(t.get("usage") is not None for t in llms)
        rows = [
            ("테스트 일시 및 시간대 / 시작·종료", f"UTC / {self.meta['시작']} ~ {self.meta['종료']}"),
            ("테스트 수행자", self.meta["수행자"]),
            ("코드 revision / 로컬 변경 여부", self.meta.get("코드", "미수집")),
            ("설정한 OPENAI_MODEL", self.meta.get("모델", "미수집")),
            ("Trace에 기록된 실제 model", sorted({t['model'] for t in llms if t.get('model')})),
            (
                "AGENT_MAX_STEPS / AGENT_MAX_TOOL_CALLS / AGENT_TIMEOUT_SECONDS",
                self.meta.get("한도", "미수집"),
            ),
            (
                "문의 ID / 생성자 UUID / 조직 UUID",
                f"{final.get('id')} / {final.get('requester_user_id')} / "
                f"{final.get('organization_id')}",
            ),
            (
                "Seed 실행 시각 / 진단 당시 UTC / Seed 만료일",
                f"{self.meta.get('Seed 시작')} ~ {self.meta.get('Seed 종료')} / "
                f"{self.meta.get('진단 요청 UTC')} / "
                f"{self.responses.get('subscription', {}).get('expires_at')}",
            ),
            (
                "진단 HTTP 코드 / 최종 status / outcome / error_code",
                f"{self.codes.get('diagnosed', '미수집')} / {final.get('status')} / "
                f"{diagnosis.get('outcome')} / {final.get('error_code')}",
            ),
            (
                "LLM 호출 수 / Tool 호출 수 / 주요 Tool 순서",
                f"{len(llms)} / {len(tools)} / {[t.get('tool_name') for t in tools]}",
            ),
            (
                "Trace usage 요약",
                f"{usage}; 제공 {provided}/{len(llms)}건 (나머지는 미제공)"
                if provided else "미제공",
            ),
            ("응답 파일 보관 위치 (공유 시 데이터 검토)", str(self.directory)),
        ]
        def cell(value):
            return str(value).replace("|", "\\|").replace("\n", "<br>")
        lines = ["# Support Agent Smoke Test 결과", "", "## 5. 결과 기록 양식", "",
                 "| 항목 | 사용자 기록 |", "|---|---|"]
        lines += [f"| {k} | {cell(v)} |" for k, v in rows]
        lines += ["", "| ID | 항목 | Pass / Fail / 미검증 | 주요 확인 결과·참조 sequence |",
                  "|---|---|---|---|"]
        lines += [f"| ST-{i:02d} | {label} | {result[0]} | {cell(result[1])} |"
                  for i, (label, result) in enumerate(zip(LABELS, results), 1)]
        failed = bool(self.failure) or any(r[0] == "Fail" for r in results)
        incomplete = any(results[i][0] != "Pass" for i in (0, 1, 3, 4))
        if failed:
            verdict = "실패"
        elif incomplete:
            verdict = "검증 미완료"
        else:
            verdict = "자동 검사 통과, 의미 검토 대기"
        failure_detail = "자동 검사 Fail 항목 참조" if failed else "없음"
        if self.failure:
            failure_detail = self.stage + " / " + self.failure
        lines += [
            "",
            "- 관찰과 최종 설명이 일치하는지에 대한 검토: 미검증. 아래 진단과 traces.json을 비교하세요.",
            f"- 실패 단계 및 확인된 원인: {failure_detail}",
            "- 미해결 사항 및 미검증 범위: 설명의 의미 일치·내부 추론·전체 품질·성능·Workflow 비교. "
            "Secret 값은 저장 전 마스킹하며 내부 추론 비노출 전수 검토는 하지 않음.",
            "- 재실행 여부·사유 및 새 문의 ID: 이 실행에서는 재시도 없음. "
            "다시 실행하면 새 문의 및 추가 비용 발생.",
            f"- 전체 판정: {verdict}. 다섯 항목 모두 Pass일 때 정상 시나리오 통과.",
            "", "## 진단 전문", "", "```json",
            json.dumps(diagnosis, ensure_ascii=False, indent=2), "```",
            "", "## HTTP 수집 결과", "", "```json",
            json.dumps(self.codes, indent=2), "```", "",
        ]
        self.write("report.md", "\n".join(lines))
        print(f"보고서: {self.directory / 'report.md'}\n판정: {verdict}")
        return 1 if failed else 2 if incomplete else 0


def main() -> int:
    os.umask(0o077)
    parent = ROOT / ".smoke-results"
    parent.mkdir(mode=0o700, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="run-", dir=parent))
    runner = Runner(directory)
    try:
        runner.run()
    except KeyboardInterrupt:
        runner.failure = "사용자 중단; 진단 POST 재시도하지 않음"
    except SmokeError as exc:
        runner.failure = str(exc)
    except Exception as exc:
        runner.failure = f"{type(exc).__name__} 발생 (원본 오류는 Secret 보호를 위해 저장하지 않음)"
    finally:
        runner.stop()
    return runner.report()


if __name__ == "__main__":
    sys.exit(main())
