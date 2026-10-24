"""离线检查追问规则、日期口径、数字引用和接口输入。"""

from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.errors import ServiceError
from app.schemas.chat import CreateConversation, QueryContext, QueryPlan, SendMessage
from app.services.analysis import month_range, previous_range, resolve_context, yuan
from app.services.chat import render_explanation


def plan(**changes):
    return QueryPlan(
        action="overview",
        month=None,
        category_mode="inherit",
        category_id=None,
        entry_type=None,
        threshold=None,
        comparison=None,
        clarification=None,
        **changes,
    )


def test_followup_preserves_month_category_and_entry_type():
    previous = QueryContext(month="2026-10", category_id="food")
    context = resolve_context(plan(), previous, date(2026, 10, 24))
    assert context == previous
    explicit = QueryPlan.model_validate(
        {**plan().model_dump(), "month": "2025-12", "category_mode": "all"}
    )
    assert resolve_context(explicit, previous, date(2026, 10, 24)) == QueryContext(month="2025-12")


@pytest.mark.parametrize(
    "changes",
    [
        {"month": "2026-13"},
        {"month": "1999-12"},
        {"category_mode": "selected"},
        {"category_mode": "selected", "category_id": "salary", "entry_type": "expense"},
        {"threshold": -1},
        {"threshold": 1000000000},
    ],
)
def test_invalid_query_plan_is_rejected(changes):
    invalid = QueryPlan.model_validate({**plan().model_dump(), **changes})
    with pytest.raises(ServiceError):
        resolve_context(invalid, QueryContext(month="2026-10"), date(2026, 10, 24))


def test_month_cutoff_leap_year_and_previous_period():
    assert month_range("2026-10", date(2026, 10, 24)) == (date(2026, 10, 1), date(2026, 10, 24))
    assert month_range("2024-02", date(2026, 10, 24))[1] == date(2024, 2, 29)
    assert previous_range(date(2026, 1, 1), date(2026, 1, 24), True) == (
        date(2025, 12, 1),
        date(2025, 12, 24),
    )
    assert previous_range(date(2024, 3, 1), date(2024, 3, 31), True)[1] == date(2024, 2, 29)
    assert previous_range(date(2026, 10, 1), date(2026, 10, 24), False)[1] == date(2026, 9, 30)


def test_exact_money_and_trusted_references():
    assert yuan(999999999) == "¥9,999,999.99"
    assert (
        render_explanation("共 {{count}} 笔，合计 {{total}}。", {"count": "3", "total": "¥8.00"})
        == "共 3 笔，合计 ¥8.00。"
    )
    for invalid in (
        "消费100元",
        "消费一百元",
        "共两笔",
        "占四成",
        "占百分之六十",
        "占比50%",
        "{{private.user}}",
        "{{total}",
    ):
        with pytest.raises(ServiceError):
            render_explanation(invalid, {"total": "¥8.00"})


def test_chat_contract_refuses_identity_and_blank_input():
    assert SendMessage(id=uuid4(), text="  看看餐饮  ").text == "看看餐饮"
    for body in (
        {"id": str(uuid4()), "text": " "},
        {"id": str(uuid4()), "text": "问题", "ownerId": str(uuid4())},
    ):
        with pytest.raises(ValidationError):
            SendMessage.model_validate(body)
    with pytest.raises(ValidationError):
        CreateConversation(id=uuid4(), book_id=uuid4(), month="2026-00")


def test_reference_expansion_cannot_overflow_saved_answer():
    with pytest.raises(ServiceError):
        render_explanation("{{row1.label}}" * 25, {"row1.label": "长备注" * 30})
