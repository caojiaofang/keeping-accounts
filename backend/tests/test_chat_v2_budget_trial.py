"""核对用户输入、分值和可继承范围。"""

import pytest

from app.schemas.chat_v2 import BudgetTrialV2
from app.services.chat_v2_budget_trial import budget_trial


@pytest.mark.parametrize(
    ("question", "budget", "spent", "result"),
    [
        ("外卖预算 600 元，已花 480 元，还剩多少？", 60000, 48000, "还剩 ¥120.00"),
        ("预算 600 元，已经花了 670 元，超支了吗？", 60000, 67000, "超支 ¥70.00"),
        ("餐饮预算 0 元，已花 12.34 元", 0, 1234, "超支 ¥12.34"),
        ("预算为零，已花 12.34 元", 0, 1234, "超支 ¥12.34"),
        ("预算0，已花0", 0, 0, "预算刚好用完，剩余 ¥0.00"),
        ("预算：￥１,２００.５０元，实际支出９９９.９９元", 120050, 99999, "还剩 ¥200.51"),
        ("预算600元和已花480元还剩多少", 60000, 48000, "还剩 ¥120.00"),
        ("外卖预算600元，实际已花480元，还能花多少？", 60000, 48000, "还剩 ¥120.00"),
        ("预算600元，已花金额未知，还剩多少？", 60000, None, "已经花了多少"),
        ("已花 480 元，预算是多少？", None, 48000, "预算是多少"),
    ],
)
def test_amounts_are_parsed_and_compared_in_integer_cents(question, budget, spent, result):
    reply = budget_trial(question, None)
    assert (reply.state.budget, reply.state.spent) == (budget, spent)
    assert "按你提供的金额" in reply.text and result in reply.text


@pytest.mark.parametrize("amount", ["-1", "42.125", "10,00", "10000000"])
def test_invalid_amount_is_not_rounded_or_applied(amount):
    reply = budget_trial(f"预算 600 元，已花 {amount} 元", None)
    assert (reply.state.budget, reply.state.spent) == (60000, None)
    assert "最多两位小数" in reply.text


@pytest.mark.parametrize(
    "question",
    [
        "预算 600 元，已花 480 元，餐饮不算",
        "外卖预算 600 元，购物已花 480 元",
        "预算 600 元，预算 800 元，已花 480 元",
        "剩余预算 600 元，已花 480 元",
    ],
)
def test_uncertain_roles_or_scope_do_not_produce_a_balance(question):
    reply = budget_trial(question, None)
    assert reply.state.budget is None and reply.state.spent is None
    assert "还剩 ¥" not in reply.text


@pytest.mark.parametrize(
    "question",
    ["每月预算 600 元怎么分？", "按账单查一下外卖，预算 600 元", "帮我设个外卖预算 600 元"],
)
def test_general_advice_and_ledger_requests_do_not_start_a_trial(question):
    assert budget_trial(question, None) is None


def test_corrections_keep_the_other_amount_only_if_the_topic_matches():
    previous = BudgetTrialV2(topic="外卖", budget=60000, spent=48000)
    corrected = budget_trial("那么，外卖预算改成 650 元呢？", previous)
    assert "还剩 ¥170.00" in corrected.text and corrected.state.topic == "外卖"
    different = budget_trial("购物预算改成 700 元", previous)
    assert different.state.spent is None and "已经花了多少" in different.text
    unnamed = budget_trial("那 700 元呢？", previous)
    assert unnamed.state == previous and "要填哪一项" in unnamed.text
    new_topic = budget_trial("帮我试算一下购物预算", previous)
    assert new_topic.state.budget is None and new_topic.state.spent is None
