from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.services import customer, policy


class AgentFailure(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class UserArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID


class OrganizationArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organization_id: UUID


ARGUMENTS = {
    "get_user": UserArguments,
    "get_organization": OrganizationArguments,
    "get_subscription": OrganizationArguments,
    "get_payment_history": OrganizationArguments,
    "search_policy": policy.PolicyQuery,
}
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": ARGUMENTS[name].model_json_schema(),
        "strict": True,
    }
    for name, description in {
        "get_user": "인증된 조직의 사용자 역할 조회",
        "get_organization": "조직의 실제 팀원 수와 한도 조회",
        "get_subscription": "조직의 구독 상태, 플랜, 만료 시각 조회",
        "get_payment_history": "조직 결제 이력 조회, 최신순",
        "search_policy": "플랜·초대·권한·결제 정책 키워드 검색",
    }.items()
]


@dataclass
class ToolResult:
    arguments: dict | None
    observation: dict


def execute_tool(
    name: str,
    raw_arguments: str,
    session: Session,
    context: customer.CallerContext,
    documents: list[policy.PolicyDocument],
    sequence: int,
) -> ToolResult:
    def result(code=None, data=None, arguments=None):
        return ToolResult(
            arguments,
            {
                "ok": code is None,
                "data": data,
                "error_code": code,
                "trace_sequence": sequence,
            },
        )

    if name not in ARGUMENTS:
        return result("unknown_tool")
    try:
        arguments = ARGUMENTS[name].model_validate_json(raw_arguments)
    except (ValidationError, TypeError, ValueError):
        return result("invalid_arguments")
    validated = arguments.model_dump(mode="json")
    try:
        customer.require_context(context)
        if name == "search_policy":
            data = policy.search_policy(arguments.query, documents)
        else:
            data = getattr(customer, name)(
                session, context, **arguments.model_dump()
            ).model_dump(mode="json")
        return result(data=data, arguments=validated)
    except customer.CustomerError as exc:
        if exc.code == "not_found":
            return result("not_found", arguments=validated)
        raise AgentFailure("tool_error") from None
    except SQLAlchemyError:
        raise
    except Exception:
        raise AgentFailure("tool_error") from None
