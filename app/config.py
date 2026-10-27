import json
import math
import os
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.engine import make_url


def timeout_settings(
    *,
    request_timeout: str | None = None,
    execution_timeout: str | None = None,
) -> dict[str, float]:
    values = {
        "llm_request_timeout_seconds": (
            request_timeout
            if request_timeout is not None
            else os.environ.get("LLM_REQUEST_TIMEOUT_SECONDS", "20")
        ),
        "agent_timeout_seconds": (
            execution_timeout
            if execution_timeout is not None
            else os.environ.get("AGENT_TIMEOUT_SECONDS", "60")
        ),
        "workflow_timeout_seconds": (
            execution_timeout
            if execution_timeout is not None
            else os.environ.get("WORKFLOW_TIMEOUT_SECONDS", "60")
        ),
    }
    try:
        parsed = {name: float(value) for name, value in values.items()}
        if any(not math.isfinite(value) or value <= 0 for value in parsed.values()):
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise ValueError("Timeout settings must be positive finite numbers") from None
    return parsed


@dataclass(frozen=True)
class Settings:
    database_url: str
    bearer_tokens: dict[str, UUID]

    openai_api_key: str | None = None
    openai_model: str | None = None
    agent_max_steps: int = 8
    agent_max_tool_calls: int = 16
    agent_timeout_seconds: float = 60
    workflow_timeout_seconds: float = 60
    llm_request_timeout_seconds: float = 20

    def __post_init__(self):
        if (
            self.agent_max_steps < 1
            or self.agent_max_tool_calls < 1
            or not math.isfinite(self.agent_timeout_seconds)
            or self.agent_timeout_seconds <= 0
        ):
            raise ValueError("Agent limits must be positive and finite")
        if (
            not math.isfinite(self.workflow_timeout_seconds)
            or self.workflow_timeout_seconds <= 0
        ):
            raise ValueError("Workflow timeout must be positive and finite")
        if (
            not math.isfinite(self.llm_request_timeout_seconds)
            or self.llm_request_timeout_seconds <= 0
        ):
            raise ValueError("LLM request timeout must be positive and finite")

    @classmethod
    def from_env(cls) -> "Settings":
        url = os.environ.get("DATABASE_URL", "")
        if not url or make_url(url).get_backend_name() != "postgresql":
            raise ValueError("DATABASE_URL must use PostgreSQL")
        try:
            raw = json.loads(os.environ.get("DEV_BEARER_TOKENS", "{}"))
            if not isinstance(raw, dict) or not raw:
                raise ValueError
            tokens = {token: UUID(user) for token, user in raw.items()}
            if any(not token or any(c.isspace() for c in token) for token in tokens):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise ValueError(
                "DEV_BEARER_TOKENS must map nonempty tokens to user UUIDs"
            ) from None
        return cls(
            url,
            tokens,
            os.environ.get("OPENAI_API_KEY") or None,
            os.environ.get("OPENAI_MODEL") or None,
            int(os.environ.get("AGENT_MAX_STEPS", "8")),
            int(os.environ.get("AGENT_MAX_TOOL_CALLS", "16")),
            **timeout_settings(),
        )
