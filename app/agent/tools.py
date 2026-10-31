from app.diagnosis.tools import ARGUMENTS


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
        "get_audit_events": (
            "인증된 조직의 변경 이력 조회, 최신순, 최대 50건. "
            "기간·행위자·대상·종류로 좁히며 total_count와 truncated로 잘림 확인"
        ),
    }.items()
]
