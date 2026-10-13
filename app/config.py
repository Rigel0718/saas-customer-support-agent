import json
import os
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.engine import make_url


@dataclass(frozen=True)
class Settings:
    database_url: str
    bearer_tokens: dict[str, UUID]

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
            raise ValueError("DEV_BEARER_TOKENS must map nonempty tokens to user UUIDs") from None
        return cls(url, tokens)
