import httpx
import openai
import pytest
from pydantic import ValidationError

from app.config import Settings, timeout_settings
from app.diagnosis import llm
from app.diagnosis.runtime import ExecutionBudget, ExecutionFailure
from app.schemas.support import LLMMetadata, TraceDTO
from app.services.support import validate_diagnosis
from tests.test_agent_loop import final
from tests.test_support_models import NOW


@pytest.fixture(autouse=True)
def clean_timeouts(monkeypatch):
    for name in (
        "LLM_REQUEST_TIMEOUT_SECONDS",
        "AGENT_TIMEOUT_SECONDS",
        "WORKFLOW_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_timeout_defaults_environment_and_positional_compatibility(monkeypatch):
    settings = Settings("unused", {}, None, None, 3, 4, 50, 55)
    assert settings.llm_request_timeout_seconds == 20
    assert settings.agent_timeout_seconds == 50
    assert settings.workflow_timeout_seconds == 55
    assert timeout_settings() == {
        "llm_request_timeout_seconds": 20,
        "agent_timeout_seconds": 60,
        "workflow_timeout_seconds": 60,
    }
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/synthetic")
    monkeypatch.setenv(
        "DEV_BEARER_TOKENS",
        '{"synthetic-token":"00000000-0000-0000-0000-000000000001"}',
    )
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECONDS", "35")
    monkeypatch.setenv("AGENT_TIMEOUT_SECONDS", "70")
    monkeypatch.setenv("WORKFLOW_TIMEOUT_SECONDS", "80")
    settings = Settings.from_env()
    assert settings.llm_request_timeout_seconds == 35
    assert settings.agent_timeout_seconds == 70
    assert settings.workflow_timeout_seconds == 80


@pytest.mark.parametrize(
    "value", ["0", "-1", "", "NaN", "inf", "-inf", "sensitive-marker"]
)
@pytest.mark.parametrize(
    "name",
    [
        "LLM_REQUEST_TIMEOUT_SECONDS",
        "AGENT_TIMEOUT_SECONDS",
        "WORKFLOW_TIMEOUT_SECONDS",
    ],
)
def test_invalid_environment_timeout_is_safe(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="positive finite numbers") as error:
        timeout_settings()
    assert "sensitive-marker" not in str(error.value)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_settings_reject_invalid_request_timeout(value):
    with pytest.raises(ValueError, match="LLM request timeout"):
        Settings("unused", {}, llm_request_timeout_seconds=value)


def test_metadata_schema_legacy_and_diagnosis_boundary():
    trace = TraceDTO(sequence=1, event_type="llm_response", created_at=NOW)
    assert trace.llm_metadata is None
    metadata = LLMMetadata(request_timeout_seconds=7)
    trace.llm_metadata = metadata
    assert TraceDTO.model_validate_json(trace.model_dump_json()) == trace
    with pytest.raises(ValidationError):
        LLMMetadata(request_timeout_seconds=7, message="sensitive-marker")
    with pytest.raises(ValidationError):
        LLMMetadata(
            request_timeout_seconds=7,
            error={
                "exception_type": "ArbitraryType",
                "is_timeout": False,
                "http_status": None,
            },
        )
    # LLM metadata is never a successful Tool observation for diagnosis evidence.
    from tests.test_agent_loop import evidence

    raw = llm.extract_output_text(final("diagnosed", [evidence(1)]))
    with pytest.raises(ExecutionFailure, match="invalid_output"):
        validate_diagnosis(raw, [trace])


def request():
    return httpx.Request(
        "POST",
        "https://sensitive-marker.invalid/responses",
        headers={"Authorization": "sensitive-marker"},
        content="sensitive-marker",
    )


def error_cases():
    cases = [
        (openai.APITimeoutError(request=request()), "APITimeoutError", True, None),
        (
            openai.APIConnectionError(message="sensitive-marker", request=request()),
            "APIConnectionError",
            False,
            None,
        ),
        (TimeoutError("sensitive-marker"), "TimeoutError", True, None),
        (ExecutionFailure("llm_error"), "ExecutionFailure", False, None),
        (
            type("sensitive-marker", (Exception,), {})("sensitive-marker"),
            "UnknownError",
            False,
            None,
        ),
    ]
    for kind, status in (
        (openai.AuthenticationError, 401),
        (openai.RateLimitError, 429),
        (openai.InternalServerError, 500),
    ):
        cases.append(
            (
                kind(
                    "sensitive-marker",
                    response=httpx.Response(
                        status,
                        request=request(),
                        headers={"sensitive-marker": "sensitive-marker"},
                        json={"error": "sensitive-marker"},
                    ),
                    body={"error": "sensitive-marker"},
                ),
                kind.__name__,
                False,
                status,
            )
        )
    return cases


@pytest.mark.parametrize("exc,label,is_timeout,status", error_cases())
def test_safe_error_classification_and_single_trace(
    exc, label, is_timeout, status, caplog
):
    traces = []
    calls = []

    def fail(**kwargs):
        calls.append(kwargs)
        raise exc

    with pytest.raises(ExecutionFailure, match="^llm_error$") as error:
        llm.call_model(
            Settings("unused", {}, llm_request_timeout_seconds=35),
            [{"role": "user", "content": "sensitive-marker"}],
            "instructions",
            traces,
            ExecutionBudget(60, lambda: 0),
            step=1,
            create=fail,
        )
    assert len(calls) == len(traces) == 1
    assert traces[0].error_code == "llm_error"
    assert traces[0].llm_metadata.model_dump() == {
        "request_timeout_seconds": 35,
        "error": {
            "exception_type": label,
            "is_timeout": is_timeout,
            "http_status": status,
        },
    }
    assert (
        "sensitive-marker"
        not in traces[0].model_dump_json() + str(error.value) + caplog.text
    )
    assert traces[0].usage is None and traces[0].model is None


@pytest.mark.parametrize(
    "status,expected",
    [(True, None), ("429", None), (99, None), (600, None), (429, 429)],
)
def test_http_status_is_a_validated_integer(status, expected):
    class CustomStatusError(openai.APIStatusError):
        pass

    exc = CustomStatusError(
        "sensitive-marker", response=httpx.Response(500, request=request()), body=None
    )
    exc.status_code = status
    metadata = llm.classify_llm_error(exc)
    assert metadata.exception_type == "APIStatusError"
    assert metadata.http_status == expected


@pytest.mark.parametrize("remaining,expected", [(50, 35), (7, 7)])
@pytest.mark.parametrize("failed", [False, True])
def test_actual_timeout_uses_remaining_budget(remaining, expected, failed):
    ticks = [0]
    budget = ExecutionBudget(60, lambda: ticks[0])
    ticks[0] = 60 - remaining
    traces, calls = [], []

    def fake(**kwargs):
        calls.append(kwargs)
        if failed:
            raise TimeoutError("sensitive-marker")
        return final()

    def call():
        return llm.call_model(
            Settings("unused", {}, llm_request_timeout_seconds=35),
            [],
            "instructions",
            traces,
            budget,
            step=1,
            create=fake,
        )

    if failed:
        with pytest.raises(ExecutionFailure, match="llm_error"):
            call()
    else:
        call()
    assert calls[0]["timeout"] == expected
    assert traces[0].llm_metadata.request_timeout_seconds == expected
    assert len(traces) == 1


def test_budget_expiry_preserves_code_and_does_not_classify_error():
    ticks = [0]
    budget = ExecutionBudget(7, lambda: ticks[0])
    traces = []

    def fake(**kwargs):
        ticks[0] = 7
        raise TimeoutError("sensitive-marker")

    with pytest.raises(ExecutionFailure, match="execution_timeout"):
        llm.call_model(
            Settings("unused", {}),
            [],
            "instructions",
            traces,
            budget,
            step=1,
            create=fake,
        )
    assert traces[0].llm_metadata.request_timeout_seconds == 7
    assert traces[0].llm_metadata.error is None
    with pytest.raises(ExecutionFailure, match="execution_timeout"):
        llm.call_model(
            Settings("unused", {}),
            [],
            "instructions",
            traces,
            budget,
            step=2,
            create=fake,
        )
    assert len(traces) == 1


def test_fake_async_client_receives_timeout_and_no_retries(monkeypatch):
    seen = {}

    class FakeClient:
        def __init__(self, **kwargs):
            seen["client"] = kwargs
            self.responses = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            seen["closed"] = True

        async def create(self, **kwargs):
            seen["request"] = kwargs
            return final()

    monkeypatch.setattr(llm, "AsyncOpenAI", FakeClient)
    traces = []
    settings = Settings(
        "unused",
        {},
        openai_api_key="synthetic-key",
        openai_model="fake-model",
        llm_request_timeout_seconds=35,
    )
    llm.call_model(
        settings, [], "instructions", traces, ExecutionBudget(7, lambda: 0), step=1
    )
    assert seen["client"]["max_retries"] == 0
    assert seen["client"]["timeout"] == seen["request"]["timeout"] == 7
    assert seen["closed"]
    assert traces[0].llm_metadata.request_timeout_seconds == 7
