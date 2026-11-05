"""评测用例和独立金额预期的离线检查，不连接数据库、Redis 或模型。"""

import asyncio
import json
from datetime import date
from uuid import uuid4

import httpx
import pytest

from app.evaluations import chat_v2
from app.evaluations.chat_v2_cases import CASES, CURRENT, RECORDS, matching, selected_cases
from app.schemas.chat_v2 import DateRange, EntryFilter, FactRef, ToolResult


def fixture_rows():
    owner, member = uuid4(), uuid4()
    return [
        {
            "id": uuid4(),
            "created_by": owner if author == "owner" else member,
            "type": kind,
            "category_id": category,
            "amount": amount,
            "date": date.fromisoformat(day),
            "note": note,
            "account": "微信",
            "is_deleted": deleted,
        }
        for _, author, kind, category, amount, day, note, deleted in RECORDS
    ]


def test_catalogue_has_three_distinct_expressions_for_all_required_scenarios():
    assert len(CASES) == 13
    assert len({case.name for case in CASES}) == len(CASES)
    assert all(len(set(case.phrases)) == 3 for case in CASES)
    assert all(0 < len(phrase) <= 200 for case in CASES for phrase in case.phrases)
    assert {case.scenario for case in CASES} == {
        "normal",
        "multi",
        "clarify",
        "permission",
        "tool_failure",
        "replay",
        "legacy",
    }
    assert selected_cases(["recent_compare", "empty"]) == [CASES[0], CASES[4]]
    with pytest.raises(ValueError, match="未知"):
        selected_cases(["invalid"])


@pytest.mark.parametrize(
    ("include", "exclude", "total", "count"),
    [
        ([], [], 40000, 8),
        (["奶茶"], [], 5500, 3),
        (["奶茶", "咖啡"], [], 9500, 5),
        (["奶茶", "咖啡"], ["退款"], 8500, 4),
        (["火星"], [], 0, 0),
    ],
)
def test_independent_oracle_separates_income_deleted_future_and_keyword_semantics(
    include, exclude, total, count
):
    filters = EntryFilter(date_range=DateRange(start=CURRENT[0], end=CURRENT[1]))
    filters.keywords.include, filters.keywords.exclude = include, exclude
    rows = matching(fixture_rows(), filters)
    assert sum(row["amount"] for row in rows) == total
    assert len(rows) == count


def test_oracle_rejects_wrong_totals_instead_of_trusting_model_or_tool():
    filters = EntryFilter(date_range=DateRange(start=CURRENT[0], end=CURRENT[1]))
    result = ToolResult(
        call_id="q1",
        name="summarize_entries",
        status="complete",
        fact_refs=[
            FactRef(
                id="q1.expense.total",
                source_call_id="q1",
                value=40000,
                unit="cents",
                label="支出合计",
                metric="total",
            )
        ],
    )
    chat_v2.oracle(result, {"filters": filters.model_dump(by_alias=True)}, fixture_rows())
    result.fact_refs[0].value = 43500  # 错误地把收入抵入或并入支出，必须被发现。
    with pytest.raises(AssertionError, match="独立预期"):
        chat_v2.oracle(result, {"filters": filters.model_dump(by_alias=True)}, fixture_rows())


def test_default_cli_only_lists_cases_without_loading_credentials(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["evaluation", "--cases", "empty"])

    def forbidden():
        raise AssertionError("离线清单不得读取配置或发起连接")

    monkeypatch.setattr(chat_v2, "Settings", forbidden)
    assert chat_v2.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "not_run" and report["real_model"] is False
    assert report["cases"][0]["name"] == "empty"


def test_parallel_http_status_metrics_do_not_mix_trials_or_log_response_bodies():
    async def exercise():
        metrics = [{}, {}]
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    503 if request.url.path == "/failure" else 200,
                    json={"private": "不可进入报告的正文"},
                )
            ),
            event_hooks={"response": [chat_v2.observe_response]},
        ) as client:

            async def request(index, path):
                token = chat_v2.REQUEST_METRIC.set(metrics[index])
                try:
                    await client.get("https://test.invalid" + path)
                finally:
                    chat_v2.REQUEST_METRIC.reset(token)

            await asyncio.gather(request(0, "/failure"), request(1, "/success"))
        assert metrics == [{"http_status": 503}, {"http_status": 200}]
        assert chat_v2.REQUEST_METRIC.get() is None

    asyncio.run(exercise())
