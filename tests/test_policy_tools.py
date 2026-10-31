import json
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.agent.tools import TOOL_DEFINITIONS
from app.diagnosis.runtime import ExecutionFailure
from app.diagnosis.tools import execute_tool
from app.seed import uid
from app.services import customer, policy


def test_policies_and_search(tmp_path):
    documents = policy.load_policies()
    assert len(documents) == 4 and len({x.document_id for x in documents}) == 4
    assert all(x.title and x.content and x.keywords for x in documents)
    assert "member_limit" in documents[0].content
    for query in (
        "초대 팀원 권한",
        "past_due failed",
        "free pro enterprise",
        " 만료  결제 ",
    ):
        result = policy.search_policy(query, documents)
        assert result["items"] and len(result["items"]) <= 3
        assert result == policy.search_policy(query, documents)
    assert [
        x["document_id"] for x in policy.search_policy("초대", documents)["items"]
    ] == ["permissions", "plans", "troubleshooting"]
    assert policy.search_policy("zzzzxyz", documents) == {"items": []}
    with pytest.raises(FileNotFoundError):
        policy.load_policies(tmp_path)


@pytest.mark.parametrize("query", ["", " ", "a" * 201, 1, None])
def test_invalid_query(query):
    with pytest.raises(ValidationError):
        policy.search_policy(query)


def context(session, user=0x101):
    return customer.authenticate(session, uid(user))


def tool(session, name, args, ctx=None):
    return execute_tool(
        name,
        json.dumps(args),
        session,
        ctx or context(session),
        policy.load_policies(),
        3,
    )


def test_tool_contract_and_direct_services(session, monkeypatch):
    for definition in TOOL_DEFINITIONS:
        assert definition["strict"] is True
        schema = definition["parameters"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
    ctx = context(session)
    for name, args in [
        ("get_user", {"user_id": str(uid(0x103))}),
        *[
            (name, {"organization_id": str(uid(1))})
            for name in ("get_organization", "get_subscription", "get_payment_history")
        ],
    ]:
        original = getattr(customer, name)
        seen = []

        def spy(s, c, **kw):
            seen.append((s, c, kw))
            return original(s, c, **kw)

        monkeypatch.setattr(customer, name, spy)
        result = tool(session, name, args, ctx)
        assert result.observation["data"] == original(
            session,
            ctx,
            **{key: uid(0x103) if key == "user_id" else uid(1) for key in args},
        ).model_dump(mode="json")
        assert seen[0][:2] == (session, ctx)
        monkeypatch.setattr(customer, name, original)


@pytest.mark.parametrize(
    "name,args,code",
    [
        ("refund", {}, "unknown_tool"),
        ("get_user", {"user_id": "bad"}, "invalid_arguments"),
        (
            "get_user",
            {"user_id": str(uid(0x101)), "role": "owner"},
            "invalid_arguments",
        ),
        ("get_user", {"user_id": str(uid(0x101)), "context": {}}, "invalid_arguments"),
        ("search_policy", {"query": " "}, "invalid_arguments"),
        ("search_policy", {"query": "結제", "extra": 1}, "invalid_arguments"),
    ],
)
def test_safe_argument_errors(session, name, args, code):
    result = tool(session, name, args)
    assert result.observation == {
        "ok": False,
        "data": None,
        "error_code": code,
        "trace_sequence": 3,
    }
    assert result.arguments is None


def test_invalid_json_and_scope(session):
    ctx = context(session)
    result = execute_tool("get_user", "{SECRET", session, ctx, [], 1)
    assert (
        result.arguments is None
        and result.observation["error_code"] == "invalid_arguments"
    )
    for name, key, other in [
        ("get_user", "user_id", uid(0x201)),
        *[
            (name, "organization_id", uid(2))
            for name in ("get_organization", "get_subscription", "get_payment_history")
        ],
    ]:
        a = tool(session, name, {key: str(other)})
        b = tool(session, name, {key: str(uuid4())})
        assert a.observation == b.observation
        assert a.observation["error_code"] == "not_found"
    assert (
        tool(
            session,
            "get_subscription",
            {"organization_id": str(uid(4))},
            context(session, 0x401),
        ).observation["error_code"]
        == "not_found"
    )
    assert tool(
        session,
        "get_payment_history",
        {"organization_id": str(uid(4))},
        context(session, 0x401),
    ).observation["data"] == {"items": []}


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("SECRET"),
        customer.CustomerError("unauthorized"),
        OperationalError("SECRET", {}, Exception("SECRET")),
    ],
)
def test_fatal_tools(session, monkeypatch, error):
    def fail(*args, **kw):
        raise error

    monkeypatch.setattr(customer, "get_user", fail)
    expected = OperationalError if isinstance(error, OperationalError) else ExecutionFailure
    with pytest.raises(expected) as exc:
        tool(session, "get_user", {"user_id": str(uid(0x101))})
    if expected is ExecutionFailure:
        assert str(exc.value) == "tool_error"


def test_search_scores_each_whitespace_token():
    documents = [
        policy.PolicyDocument(
            document_id="a", title="alpha", keywords=[], content="alpha"
        ),
        policy.PolicyDocument(
            document_id="b", title="beta", keywords=[], content="beta"
        ),
    ]
    assert [
        doc["document_id"]
        for doc in policy.search_policy("alpha beta beta", documents)["items"]
    ] == ["b", "a"]
