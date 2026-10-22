"""User-run Workflow smoke runner with development DB and Backend preparation."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

# Also support the documented `python scripts/workflow_smoke.py` invocation.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.engine import make_url

from app.config import Settings
from app.diagnosis.runtime import ExecutionFailure
from app.schemas.support import RequestDTO, TracesDTO
from app.services.support import validate_diagnosis
from scripts.support_agent_smoke import CONTENT, MEMBER, ORG, Runner, SmokeError, now

STEPS = [
    ("get_user", {"user_id": MEMBER}),
    ("get_organization", {"organization_id": ORG}),
    ("get_subscription", {"organization_id": ORG}),
    ("get_payment_history", {"organization_id": ORG}),
    ("search_policy", {"query": "플랜"}),
    ("search_policy", {"query": "권한"}),
    ("search_policy", {"query": "구독 결제"}),
]
SAFE_ERRORS = {
    "llm_error", "tool_error", "execution_timeout", "invalid_output",
    "model_refusal", "database_error",
}
LABELS = [
    "실행 방식", "고객 4회·정책 3회 고정 조회", "LLM 응답 정확히 1회",
    "동적 Tool 실행 없음 (Trace 기준)", "정상 완료·오류 없음",
    "Diagnosis 구조·Evidence 참조", "Trace sequence·종료 일관성",
    "POST/GET 저장 일치", "실패 시 안전한 오류·Trace 보존",
]


def assess(post, retrieved, traces):
    results = {label: ["미검증", "응답 미수집"] for label in LABELS}

    def check(index, ok, detail):
        results[LABELS[index]] = ["Pass" if ok else "Fail", detail]

    try:
        items = TracesDTO.model_validate(traces).items if traces is not None else None
    except ValueError:
        check(6, False, "Trace 응답 형식 오류")
        items = None
    try:
        dto = RequestDTO.model_validate(retrieved) if retrieved is not None else None
    except ValueError:
        check(4, False, "문의 응답 형식 오류")
        dto = None
    if dto:
        check(0, dto.execution_mode == "workflow", str(dto.execution_mode))
        completed = (
            dto.status == "completed"
            and dto.error_code is None
            and dto.diagnosis is not None
            and str(dto.requester_user_id) == MEMBER
            and str(dto.organization_id) == ORG
            and dto.content == CONTENT
        )
        check(4, completed, f"status={dto.status}, error_code={dto.error_code}")
        if items is not None:
            valid = False
            if completed:
                try:
                    validate_diagnosis(json.dumps(retrieved["diagnosis"]), items)
                    valid = True
                except ExecutionFailure:
                    pass
            check(5, valid, "구조와 관찰·정책 ID 참조만 검사; 의미 검토 별도")
    if items is not None:
        tools = [t for t in items if t.event_type == "tool_result"]
        llms = [t for t in items if t.event_type == "llm_response"]
        tool_ok = len(tools) == len(STEPS)
        for t, (name, arguments) in zip(tools, STEPS):
            obs = t.observation or {}
            tool_ok &= (
                t.tool_name == name and t.arguments == arguments
                and obs.get("ok") is True and obs.get("error_code") is None
                and t.error_code is None and t.call_id is None
                and obs.get("trace_sequence") == t.sequence
                and isinstance(obs.get("data"), dict)
            )
        check(1, tool_ok, str([(t.sequence, t.tool_name, t.error_code) for t in tools]))
        llm_ok = (
            len(llms) == 1 and llms[0].error_code is None
            and bool(llms[0].model) and bool(llms[0].response_id)
        )
        check(2, llm_ok, f"기록된 LLM 응답/시도 {len(llms)}회")
        check(
            3,
            bool(
                llm_ok and tool_ok
                and all(t.sequence < llms[0].sequence for t in tools)
                and llms[0].tool_name is None and llms[0].call_id is None
            ),
            "7개 고정 조회가 모두 단일 LLM 응답보다 앞섬; 원본 공급자 요청은 수집하지 않음",
        )
        events = [t.event_type for t in items]
        common = (
            bool(items) and [t.sequence for t in items] == list(range(1, len(items) + 1))
            and events[0] == "execution_started"
            and all(t.duration_ms is not None and t.duration_ms >= 0 for t in tools + llms)
            and all(items[i].created_at <= items[i + 1].created_at for i in range(len(items) - 1))
        )
        terminal_ok = False
        if dto and dto.started_at and dto.finished_at:
            common &= (
                dto.created_at <= dto.started_at <= dto.finished_at
                and bool(items)
                and dto.started_at <= items[0].created_at <= items[-1].created_at <= dto.finished_at
            )
            if dto.status == "completed":
                terminal_ok = (
                    events == ["execution_started"] + ["tool_result"] * 7
                    + ["llm_response", "final_result", "execution_completed"]
                    and [t.step for t in tools + llms] == list(range(1, 9))
                    and all(t.error_code is None for t in items)
                )
            elif dto.status == "failed":
                terminal_ok = (
                    events[-1:] == ["execution_failed"]
                    and events.count("execution_failed") == 1
                    and "execution_completed" not in events
                    and items[-1].error_code == dto.error_code
                )
        check(6, bool(common and terminal_ok), f"{len(items)}건; 종료={events[-2:]}")
        if dto and dto.status == "failed":
            check(8, bool(common and terminal_ok and dto.error_code in SAFE_ERRORS
                          and dto.diagnosis is None), "안전한 오류 코드와 실패 종료 Trace 검사")
        elif dto and dto.status == "completed":
            results[LABELS[8]] = ["미검증", "정상 실행으로 실패 경로 미발생 (전체 판정 필수 항목에서 제외)"]
    if post is not None and retrieved is not None:
        check(7, post == retrieved, "진단 POST와 GET 전체 응답 비교")
    return results


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise SmokeError("HTTP redirect 거부: Backend URL을 확인하세요")


class WorkflowRunner(Runner):
    def __init__(self, directory):
        super().__init__(directory)
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.diagnosis_attempted = False
        self.elapsed = {}
        self.started_clock = time.monotonic()

    def write(self, name, value):
        # Never retain authentication fields even if a backend accidentally echoes them.
        def scrub(item):
            if isinstance(item, dict):
                return {
                    k: scrub(v)
                    for k, v in item.items()
                    if not any(
                        word in k.casefold()
                        for word in ("authorization", "password", "token", "api_key", "secret")
                    ) or k in {"input_tokens", "output_tokens", "total_tokens"}
                }
            if isinstance(item, list):
                return [scrub(v) for v in item]
            return item
        super().write(name, scrub(value) if not isinstance(value, str) else value)

    def clean(self, text):
        for value in sorted(set(self.hidden), key=len, reverse=True):
            if value:
                text = text.replace(json.dumps(value, ensure_ascii=False)[1:-1], "[REDACTED]")
        return super().clean(text)

    def http(self, name, method, path, body=None, timeout=15):
        if method == "POST" and "/diagnose" in path:
            if self.diagnosis_attempted:
                raise SmokeError("중복 진단 요청 차단")
            self.diagnosis_attempted = True
        before = time.monotonic()
        try:
            return super().http(name, method, path, body, timeout)
        finally:
            self.elapsed[name] = round(time.monotonic() - before, 3)
            self.codes.setdefault(name, "연결 실패/응답 없음")
            # The creation response may have arrived before a local file write failed.
            if name == "created" and self.codes.get(name) == 201:
                try:
                    dto = RequestDTO.model_validate(self.responses.get(name))
                    self.path = f"/support/requests/{dto.id}"
                except ValueError:
                    pass
            print(f"{name}: HTTP {self.codes[name]}", flush=True)

    def require(self, name, status):
        result = super().require(name, status)
        print(f"성공: {name}", flush=True)
        return result

    def stage_start(self, name):
        self.stage = name
        print(f"진행: {name}", flush=True)

    def recover(self):
        if not hasattr(self, "path"):
            return
        for name, suffix in (("retrieved", ""), ("traces", "/traces")):
            try:
                self.http(name, "GET", self.path + suffix)
                self.require(name, 200)
            except Exception:
                self.responses.pop(name, None)
                print(f"실패: {name} 수집 (재시도 없음)", flush=True)

    def run(self):
        self.stage_start("설정 확인")
        # Register secrets before any network/file operations; never read .env itself.
        for key, value in os.environ.items():
            if any(part in key.upper() for part in ("KEY", "TOKEN", "PASSWORD", "SECRET", "DATABASE_URL")):
                self.hidden.append(value)
        self.settings = Settings.from_env()
        self.hidden.extend(self.settings.bearer_tokens)
        self.hidden.extend(filter(None, [
            self.settings.openai_api_key,
            make_url(self.settings.database_url).password,
        ]))
        self.stage_start("PostgreSQL 기동")
        self.command(["docker", "compose", "up", "-d", "--wait", "postgres"])
        self.stage_start("테이블 생성·기존 Workflow 스키마 이행")
        self.command([sys.executable, "-m", "scripts.prepare_workflow_smoke_db"])
        self.stage_start("Seed 갱신")
        self.meta["Seed 시작"] = now()
        self.command([sys.executable, "-m", "app.seed"])
        self.meta["Seed 종료"] = now()
        self.stage_start("임시 Backend 시작")
        self.start_backend()
        self.stage_start("Backend 연결 확인")
        self.http("backend", "GET", "/openapi.json")
        self.require("backend", 200)
        self.meta["설정 모델"] = self.settings.openai_model or "미설정"
        revision = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT,
            capture_output=True, text=True, check=True,
        ).stdout
        self.meta["로컬 코드"] = f"{revision}; 로컬 변경={bool(dirty)} (Backend revision 미검증)"
        self.stage_start("Seed 고객·조직·구독·결제 조회")
        for name, path in {
            "user": f"/users/{MEMBER}", "organization": f"/organizations/{ORG}",
            "subscription": f"/organizations/{ORG}/subscription",
            "payments": f"/organizations/{ORG}/payments",
        }.items():
            self.http(name, "GET", path)
            self.require(name, 200)
        if (
            self.responses["user"].get("role") != "member"
            or self.responses["user"].get("organization_id") != ORG
        ):
            raise SmokeError("Seed member·조직 불일치")
        print("확인 시 새 문의 1건을 생성하고 실제 OpenAI 진단을 1회 요청합니다. 비용이 발생할 수 있습니다.\n"
              "반복 실행하면 새 문의와 추가 비용이 발생합니다.", flush=True)
        try:
            confirmed = input("실행하려면 RUN WORKFLOW 입력: ") == "RUN WORKFLOW"
        except EOFError:
            confirmed = False
        if not confirmed:
            raise SmokeError("실행 확인 없음: 문의 생성·진단 요청하지 않음")
        self.stage_start("문의 한 건 생성")
        created = self.http("created", "POST", "/support/requests", {"content": CONTENT})
        self.require("created", 201)
        dto = RequestDTO.model_validate(created)
        self.path = f"/support/requests/{dto.id}"
        if (
            dto.status != "pending" or str(dto.requester_user_id) != MEMBER
            or str(dto.organization_id) != ORG or dto.content != CONTENT
            or any(
                created[k] is not None
                for k in ("started_at", "finished_at", "diagnosis", "error_code")
            )
        ):
            raise SmokeError("문의 생성 상태·대상 불일치")
        before = self.http("before", "GET", self.path)
        self.require("before", 200)
        traces = self.http("before-traces", "GET", self.path + "/traces")
        self.require("before-traces", 200)
        if before != created or traces != {"items": []}:
            raise SmokeError("진단 전 상태 불일치: 실행 중단")
        self.stage_start("Workflow 진단 1회 및 저장 결과 재조회")
        try:
            self.http("diagnosed", "POST", self.path + "/diagnose?mode=workflow",
                      timeout=self.settings.workflow_timeout_seconds + 90)
        finally:
            self.recover()  # POST is never retried, even after disconnection or write failure.
        self.require("diagnosed", 200)
        self.stage_start("자동 검사·보고서 생성")

    def report(self):
        def successful(name):
            return self.responses.get(name) if self.codes.get(name) == 200 else None

        post = successful("diagnosed")
        retrieved = successful("retrieved")
        traces = successful("traces")
        results = assess(post, retrieved, traces)
        created = self.responses.get("created")
        if isinstance(created, dict) and isinstance(retrieved, dict):
            if retrieved.get("id") != created.get("id"):
                results[LABELS[7]] = ["Fail", "생성한 문의와 재조회 문의 ID 불일치"]
        failed = bool(self.failure) or any(value[0] == "Fail" for value in results.values())
        incomplete = any(results[label][0] != "Pass" for label in LABELS[:8])
        verdict = "실패" if failed else "검증 미완료" if incomplete else "의미 검토 대기"
        final = next(
            (value for value in (retrieved, post, created) if isinstance(value, dict)),
            {},
        )
        try:
            items = TracesDTO.model_validate(traces).model_dump(mode="json")["items"]
        except ValueError:
            items = []
        llms = [t for t in items if t["event_type"] == "llm_response"]
        tools = [t for t in items if t["event_type"] == "tool_result"]
        usage = [{"sequence": t["sequence"], "usage": t["usage"] or "미제공"} for t in llms]
        duration = "미수집"
        if retrieved:
            try:
                dto = RequestDTO.model_validate(retrieved)
                if dto.finished_at and dto.started_at:
                    duration = (dto.finished_at - dto.started_at).total_seconds()
            except ValueError:
                pass
        details = {
            **self.meta, "종료 UTC": now(), "실행 방식": final.get("execution_mode", "미수집"),
            "실제 모델 (Trace)": [t["model"] for t in llms],
            "문의 ID": final.get("id"), "생성자": final.get("requester_user_id"),
            "조직": final.get("organization_id"), "status": final.get("status"),
            "error_code": final.get("error_code"), "LLM 응답/시도 수": len(llms),
            "Tool 수": len(tools), "Token Usage (null은 미제공)": usage,
            "문의 실행 시간(초)": duration, "Runner 경과(초)": round(time.monotonic() - self.started_clock, 3),
            "HTTP 소요(초)": self.elapsed, "HTTP 상태": self.codes,
        }
        def cell(value):
            return str(value).replace("|", "\\|").replace("\n", "<br>")
        lines = ["# Workflow Smoke Test 결과", "", f"전체 판정: **{verdict}**", "",
                 "```json", json.dumps(details, ensure_ascii=False, indent=2), "```", "",
                 "| 자동 검증 | 결과 | 상세 |", "|---|---|---|"]
        lines += [f"| {label} | {state} | {cell(detail)} |" for label, (state, detail) in results.items()]
        lines += ["", "## Workflow 조회 순서", "", "| sequence | Tool | 인자 | 성공 | 소요 ms |", "|---|---|---|---|---|"]
        lines += [f"| {t['sequence']} | {t['tool_name']} | {cell(t['arguments'])} | "
                  f"{cell((t['observation'] or {}).get('ok'))} | {t['duration_ms']} |" for t in tools]
        lines += ["", "## Diagnosis 및 Evidence", "", "```json",
                  json.dumps(final.get("diagnosis"), ensure_ascii=False, indent=2), "```", "",
                  "## 사용자 의미 검토 (미검증)", "",
                  "- [ ] Diagnosis의 원인·요약·다음 행동이 고객 관찰과 일치한다.",
                  "- [ ] 정책 해석과 Evidence의 설명이 실제 정책 관찰에 부합한다.",
                  "- [ ] 구독 만료일과 진단 시각, 권한·팀원 한도·결제를 함께 검토했다.",
                  "- [ ] needs_information/escalate인 경우 추가 질문·이관이 적절하다.",
                  "- 검토자 / 검토 일시 / 근거 / 최종 판정: 사용자 기록 필요", "",
                  "## 실패 원인·미해결 사항", "",
                  f"- 실패 단계·원인: {self.stage + ' / ' + self.failure if self.failure else '자동 Fail 항목 참조' if failed else '없음'}",
                  "- 동적 Tool 실행 여부는 Trace와 workflow 완료 계약을 기준으로 검사한다. "
                  "공급자에 보낸 tools 옵션과 거부된 function_call 원문은 Trace에 없어 직접 검증하지 못한다.",
                  "- 실패하지 않은 실행은 실패 경로 미검증이다. usage 미제공 값은 0으로 추정하지 않는다.",
                  "- 진단 재시도 없음. 연결 단절 후 GET은 각 1회이며 running·빈 Trace가 남을 수 있다.",
                  "- 다시 실행하면 새 문의·추가 비용이 발생할 수 있다. JSON에는 고객 데이터가 포함된다.", ""]
        self.write("report.md", "\n".join(lines))
        print(f"보고서: {self.directory / 'report.md'}\n판정: {verdict}")
        return 1 if failed else 2 if incomplete else 0


def main():
    os.umask(0o077)
    runner = None
    try:
        parent = ROOT / ".smoke-results"
        parent.mkdir(mode=0o700, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="workflow-run-", dir=parent))
        runner = WorkflowRunner(directory)
        try:
            runner.run()
        except KeyboardInterrupt:
            runner.failure = "사용자 중단; 진단 재시도 없음"
        except SmokeError as exc:
            runner.failure = str(exc)
        except Exception as exc:
            runner.failure = f"{type(exc).__name__} 발생 (Secret 보호를 위해 원문 생략)"
        if runner.failure:
            print(f"실패: {runner.stage} / {runner.failure}", flush=True)
            if hasattr(runner, "path") and "retrieved" not in runner.codes:
                runner.recover()
        return runner.report()
    except Exception:
        print("실패: 결과 디렉터리 또는 보고서 저장 불가 (오류 원문 생략).", file=sys.stderr)
        if runner:
            print(f"보존된 파일: {runner.directory}", file=sys.stderr)
        return 1
    finally:
        if runner:
            runner.stop()


if __name__ == "__main__":
    sys.exit(main())
