import asyncio
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)

from app.config import Settings
from app.diagnosis.runtime import ExecutionBudget, ExecutionFailure, add_trace
from app.schemas.support import (
    DiagnosisDTO,
    FailureMetadata,
    LLMErrorMetadata,
    LLMMetadata,
    TraceDTO,
)


def classify_llm_error(exc: Exception) -> LLMErrorMetadata:
    # Only fixed labels and a validated status number leave this boundary.
    # Never inspect messages, request/response objects, or exception chains.
    labels = (
        (APITimeoutError, "APITimeoutError"),
        (APIConnectionError, "APIConnectionError"),
        (BadRequestError, "BadRequestError"),
        (AuthenticationError, "AuthenticationError"),
        (PermissionDeniedError, "PermissionDeniedError"),
        (NotFoundError, "NotFoundError"),
        (ConflictError, "ConflictError"),
        (UnprocessableEntityError, "UnprocessableEntityError"),
        (RateLimitError, "RateLimitError"),
        (InternalServerError, "InternalServerError"),
        (APIStatusError, "APIStatusError"),
        (TimeoutError, "TimeoutError"),
        (ExecutionFailure, "ExecutionFailure"),
    )
    label = next(
        (label for kind, label in labels if isinstance(exc, kind)), "UnknownError"
    )
    status = exc.status_code if isinstance(exc, APIStatusError) else None
    if type(status) is not int or not 100 <= status <= 599:
        status = None
    return LLMErrorMetadata(
        exception_type=label,
        is_timeout=isinstance(exc, (APITimeoutError, TimeoutError)),
        http_status=status,
    )


def responses_create(
    settings: Settings, *, budget_seconds: float | None = None, **kwargs
):
    if not settings.openai_api_key or not settings.openai_model:
        raise ExecutionFailure("llm_error")
    deadline = time.monotonic() + (
        budget_seconds if budget_seconds is not None else kwargs["timeout"]
    )

    async def request():
        # 동기 Loop의 호출 경계에서만 취소 가능한 HTTP I/O를 사용한다.
        limit = asyncio.timeout(max(0, deadline - time.monotonic()))
        try:
            async with limit:
                async with AsyncOpenAI(
                    api_key=settings.openai_api_key,
                    max_retries=0,
                    timeout=kwargs["timeout"],
                ) as client:
                    return await client.responses.create(**kwargs)
        except TimeoutError:
            if limit.expired():
                raise ExecutionFailure("execution_timeout") from None
            raise

    return asyncio.run(request())


def as_dict(value: Any) -> dict:
    return (
        value
        if isinstance(value, dict)
        else value.model_dump(mode="json", exclude_none=True)
    )


def call_model(
    settings: Settings,
    inputs: list[dict],
    instructions: str,
    traces: list[TraceDTO],
    budget: ExecutionBudget,
    *,
    step: int,
    create: Callable | None = None,
    now: Callable = lambda: datetime.now(timezone.utc),
    **request_options,
) -> dict:
    timeout = min(settings.llm_request_timeout_seconds, budget.remaining())
    before = budget.clock()
    try:
        response = (
            create
            or (
                lambda **kwargs: responses_create(
                    settings, budget_seconds=budget.remaining(), **kwargs
                )
            )
        )(
            model=settings.openai_model,
            instructions=instructions,
            input=inputs,
            store=False,
            timeout=timeout,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "support_diagnosis",
                    "strict": True,
                    "schema": DiagnosisDTO.model_json_schema(),
                }
            },
            **request_options,
        )
    except Exception as exc:
        expired = isinstance(exc, ExecutionFailure) and exc.code == "execution_timeout"
        code = (
            "execution_timeout"
            if expired or budget.clock() >= budget.deadline
            else "llm_error"
        )
        add_trace(
            traces,
            "llm_response",
            now,
            step=step,
            error_code=code,
            duration_ms=max(0, int((budget.clock() - before) * 1000)),
            llm_metadata=LLMMetadata(
                request_timeout_seconds=timeout,
                error=classify_llm_error(exc) if code == "llm_error" else None,
            ),
        )
        raise ExecutionFailure(code) from None
    data = as_dict(response)
    usage = data.get("usage")
    add_trace(
        traces,
        "llm_response",
        now,
        step=step,
        model=data.get("model"),
        response_id=data.get("id"),
        llm_metadata=LLMMetadata(request_timeout_seconds=timeout),
        usage=(
            {
                name: usage.get(name)
                for name in ("input_tokens", "output_tokens", "total_tokens")
            }
            if isinstance(usage, dict)
            else None
        ),
        duration_ms=max(0, int((budget.clock() - before) * 1000)),
    )
    budget.remaining()
    output = data.get("output", [])
    if any(
        part.get("type") == "refusal"
        for item in output
        for part in item.get("content", [])
        if isinstance(part, dict)
    ):
        raise ExecutionFailure("model_refusal")
    if data.get("status") != "completed":
        raise ExecutionFailure(
            "invalid_output",
            failure_metadata=FailureMetadata(
                failure_stage="response_status", failure_reason="not_completed"
            ),
        )
    return data


def extract_output_text(data: dict) -> str:
    return "".join(
        part.get("text", "")
        for item in data.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    )
