"""四个只读工具、事实一致性、游标绑定和失败脱敏；使用实际内存 SQL。"""

import asyncio
from datetime import date
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from test_analysis_v2_queries import AsyncReader
from test_analysis_v2_queries import data as data

from app.core.config import Settings
from app.core.errors import ServiceError
from app.models.ledger import Book, LedgerEntry
from app.schemas.chat_v2 import ToolResult
from app.services.chat_v2_tools import ARGUMENTS, ChatToolsV2, basis_points
from app.services.ledger import LedgerService

NOW = 1793156400000


def tools(data, *, secret="cursor-test-key-" * 3, clock=lambda: NOW):
    return ChatToolsV2(
        data.ledger,
        Settings(_env_file=None, chat_v2_cursor_secret=SecretStr(secret) if secret else None),
        today=date(2026, 10, 27),
        clock=clock,
    )


def run(data, name, *, call_id="q1", runner=None, book=None, **arguments):
    if name != "get_entry":
        arguments = {
            "filters": {
                "dateRange": {"start": "2026-10-21", "end": "2026-10-27"},
                **arguments.pop("filters", {}),
            },
            **arguments,
        }
    result = asyncio.run(
        (runner or tools(data)).execute(call_id, book or data.books["shared"], name, arguments)
    )
    assert ToolResult.model_validate_json(result.model_dump_json()) == result
    return result


def facts(result):
    return {fact.id: fact.value for fact in result.fact_refs}


@pytest.mark.parametrize("group_by", [None, "category", "date", "account", "creator"])
def test_summary_all_groups_and_fact_metadata(data, group_by):
    result = run(data, "summarize_entries", groupBy=group_by)
    assert result.status == "complete"
    values = facts(result)
    assert values["q1.expense.total"] == 47501
    assert values["q1.expense.count"] == 6
    if group_by:
        totals = [
            values[ref]
            for row in result.cards[0].rows
            for ref in row.fact_ids
            if ref.endswith(".total")
        ]
        assert sum(totals) == 47501
    for fact in result.fact_refs:
        assert fact.generated_at == NOW
        assert fact.source_call_id == "q1"
        assert fact.date_range is not None
    assert result.cards[0].type == ("trend" if group_by == "date" else "summary")


def test_summary_search_and_comparison_totals_agree(data):
    summary = run(data, "summarize_entries", filters={"categoryIds": ["coffee", "food"]})
    search = run(data, "search_entries", filters={"categoryIds": ["coffee", "food"]})
    compare = run(
        data,
        "compare_periods",
        filters={"categoryIds": ["coffee", "food"]},
        previousRange={"start": "2026-10-14", "end": "2026-10-20"},
    )
    for result in (summary, search, compare):
        assert result.status == "complete"
        assert facts(result)["q1.expense.total"] == 7500
        assert facts(result)["q1.expense.count"] == 4
    values = facts(compare)
    assert values["q1.expense.previous_total"] == 1600
    assert values["q1.expense.difference"] == 5900
    assert values["q1.expense.change_rate"] == 36875
    assert (
        sum(
            values[ref]
            for row in compare.cards[0].rows
            for ref in row.fact_ids
            if ref.endswith(".difference")
        )
        == 5900
    )


def test_search_real_ids_matching_evidence_and_single_read(data):
    result = run(data, "search_entries", filters={"keywords": {"include": ["咖啡", "coffee"]}})
    assert result.status == "complete"
    assert {row.entry_id for row in result.cards[0].rows} == {data.ids["combo"], data.ids["coffee"]}
    coffee = next(row for row in result.cards[0].rows if row.entry_id == data.ids["coffee"])
    assert coffee.matched_keywords == ["咖啡", "coffee"]
    assert coffee.creator_id == data.users["owner"] and coffee.account == "支付宝"
    detail = run(data, "get_entry", entryId=str(coffee.entry_id))
    assert detail.status == "complete"
    assert detail.cards[0].rows[0].entry_id == coffee.entry_id
    assert facts(detail)["q1.expense.total"] == 2000


@pytest.mark.parametrize("name", ["summarize_entries", "search_entries", "compare_periods"])
def test_empty_queries_are_complete_zero_results(data, name):
    args = {"filters": {"keywords": {"include": ["没有这样的账单"]}}}
    if name == "compare_periods":
        args["previousRange"] = {"start": "2026-09-01", "end": "2026-09-30"}
    result = run(data, name, **args)
    assert result.status == "complete"
    assert facts(result)["q1.expense.total"] == 0
    assert facts(result)["q1.expense.count"] == 0
    assert result.cards[0].rows == [] and result.next_cursor is None
    assert "q1.expense.change_rate" not in facts(result)


def test_refund_is_income_and_both_keeps_totals_separate(data):
    data.session.add(
        LedgerEntry(
            id=uuid4(),
            book_id=data.books["shared"],
            created_by=data.users["owner"],
            type="income",
            category_id="refund",
            amount=900,
            date=date(2026, 10, 26),
            note="退款",
            account="微信",
        )
    )
    data.session.commit()
    for name in ("summarize_entries", "search_entries", "compare_periods"):
        args = {"filters": {"entryType": "both"}}
        if name == "compare_periods":
            args["previousRange"] = {"start": "2026-09-01", "end": "2026-09-30"}
        result = run(data, name, **args)
        assert result.status == "complete"
        assert facts(result)["q1.expense.total"] == 47501
        assert facts(result)["q1.income.total"] == 50900
        assert "q1.total" not in facts(result)


def seed_pages(data):
    data.session.query(LedgerEntry).filter(LedgerEntry.book_id == data.books["shared"]).delete()
    ids = []
    for index in range(45):
        eid = UUID(int=1000 + index)
        ids.append(eid)
        data.session.add(
            LedgerEntry(
                id=eid,
                book_id=data.books["shared"],
                created_by=data.users["owner"],
                type="expense",
                category_id="coffee",
                amount=100,
                date=date(2026, 10, 26),
                time=None if index % 2 else "12:00",
                note="分页",
                account="微信",
            )
        )
    data.session.commit()
    return ids


@pytest.mark.parametrize("sort", ["date_desc", "amount_desc"])
def test_keyset_paging_same_dates_amounts_and_missing_times(data, sort):
    expected = seed_pages(data)
    seen, cursor = [], None
    for page in range(3):
        result = run(data, "search_entries", sort=sort, cursor=cursor, call_id=f"p{page}")
        assert result.status == "complete"
        assert facts(result)[f"p{page}.expense.total"] == 4500
        assert facts(result)[f"p{page}.expense.count"] == 45
        assert len(result.cards[0].rows) == (20 if page < 2 else 5)
        seen += [row.entry_id for row in result.cards[0].rows]
        cursor = result.next_cursor
        assert bool(cursor) == (page < 2)
    if sort == "date_desc":
        expected.sort(
            key=lambda eid: ("12:00" if (eid.int - 1000) % 2 == 0 else "", eid.int), reverse=True
        )
    else:
        expected.sort(reverse=True)
    assert seen == expected and len(set(seen)) == 45


@pytest.mark.parametrize(
    "change", ["tamper", "filter", "sort", "limit", "owner", "book", "expired", "secret"]
)
def test_cursor_rejects_changed_scope_tampering_and_expiry(data, change):
    seed_pages(data)
    first = run(data, "search_entries")
    cursor = first.next_cursor
    arguments, runner, book = {}, None, None
    if change == "tamper":
        cursor = ("A" if cursor[0] != "A" else "B") + cursor[1:]
    elif change == "filter":
        arguments["filters"] = {"accounts": ["微信"]}
    elif change == "sort":
        arguments["sort"] = "amount_desc"
    elif change == "limit":
        arguments["limit"] = 10
    elif change == "owner":
        data.ledger = LedgerService(AsyncReader(data.session), data.users["member"])
    elif change == "book":
        book = data.books["personal"]
    elif change == "expired":
        runner = tools(data, clock=lambda: NOW + 1801000)
    else:
        runner = tools(data, secret="different-cursor-secret-" * 2)
    result = run(data, "search_entries", cursor=cursor, runner=runner, book=book, **arguments)
    assert result.status == "error" and result.error.code == "invalid_arguments"
    assert result.cards == [] and result.fact_refs == []


def test_missing_pagination_key_does_not_silently_truncate(data):
    seed_pages(data)
    result = run(data, "search_entries", runner=tools(data, secret=None))
    assert result.status == "error" and result.error.code == "unavailable"
    assert not result.cards


def test_negative_group_contribution_and_zero_baseline(data):
    data.session.add(
        LedgerEntry(
            id=uuid4(),
            book_id=data.books["shared"],
            created_by=data.users["owner"],
            type="expense",
            category_id="shopping",
            amount=50000,
            date=date(2026, 10, 14),
            note="以前购物",
            account="微信",
        )
    )
    data.session.commit()
    result = run(
        data, "compare_periods", previousRange={"start": "2026-10-14", "end": "2026-10-20"}
    )
    values = facts(result)
    assert values["q1.expense.difference"] == -4099
    differences = [
        values[ref]
        for row in result.cards[0].rows
        for ref in row.fact_ids
        if ref.endswith(".difference")
    ]
    assert (
        sum(differences) == -4099
        and any(value < 0 for value in differences)
        and any(value > 0 for value in differences)
    )
    zero = run(data, "compare_periods", previousRange={"start": "2026-09-01", "end": "2026-09-30"})
    assert "q1.expense.change_rate" not in facts(zero)
    assert "上期金额为零" in zero.cards[0].footnote


@pytest.mark.parametrize("group_by", ["category", "date", "account", "creator"])
def test_compare_group_dimensions(data, group_by):
    result = run(
        data,
        "compare_periods",
        groupBy=group_by,
        previousRange={"start": "2026-10-14", "end": "2026-10-20"},
    )
    assert result.status == "complete"
    values = facts(result)
    assert (
        sum(
            values[ref]
            for row in result.cards[0].rows
            for ref in row.fact_ids
            if ref.endswith(".difference")
        )
        == values["q1.expense.difference"]
    )


@pytest.mark.parametrize("key", ["foreign", "other", "deleted", "future", "random"])
def test_entry_read_does_not_disclose_invalid_or_out_of_scope_ids(data, key):
    result = run(data, "get_entry", entryId=str(data.ids.get(key, uuid4())))
    assert result.status == "error" and result.error.code == "not_found"
    assert result.error.detail == "账单不存在或无权访问"


def test_validation_registry_failures_and_database_errors_are_safe(data):
    assert set(ARGUMENTS) == {"summarize_entries", "search_entries", "compare_periods", "get_entry"}
    invalid = run(data, "search_entries", sql="select private")
    assert invalid.status == "error" and "private" not in invalid.model_dump_json()
    with pytest.raises(ServiceError):
        asyncio.run(tools(data).execute("q1", data.books["shared"], "delete_entries", {}))
    data.ledger.session.execute = AsyncMock(side_effect=SQLAlchemyError("private credentials"))
    failed = run(data, "search_entries")
    assert failed.status == "error" and failed.error.code == "unavailable"
    assert "private" not in failed.model_dump_json()


def test_permission_change_after_business_query_does_not_return_statistics(data):
    runner = tools(data)
    original = data.ledger.require_book
    calls = 0

    async def require(bid):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise ServiceError(404, "账本不存在或无权访问")
        return await original(bid)

    data.ledger.require_book = require
    result = run(data, "summarize_entries", runner=runner)
    assert result.status == "error" and not result.cards and not result.fact_refs


def test_read_transactions_are_released_and_dissolved_books_are_not_read(data):
    run(data, "search_entries")
    assert data.session.in_transaction() is False
    data.session.execute(
        update(Book).where(Book.id == data.books["shared"]).values(status="dissolved")
    )
    data.session.commit()
    result = run(data, "summarize_entries")
    assert result.status == "error" and result.error.code == "not_found"


def test_more_than_twenty_groups_keep_all_totals_and_distinguish_other(data):
    ids = seed_pages(data)
    for index, eid in enumerate(ids):
        data.session.execute(
            update(LedgerEntry)
            .where(LedgerEntry.id == eid)
            .values(
                account=f"a{index:02}",
            )
        )
    data.session.execute(
        update(LedgerEntry)
        .where(LedgerEntry.id == ids[0])
        .values(
            account="remaining",
            amount=1000,
        )
    )
    data.session.commit()
    for name in ("summarize_entries", "compare_periods"):
        args = {"groupBy": "account"}
        if name == "compare_periods":
            args["previousRange"] = {"start": "2026-09-01", "end": "2026-09-30"}
        result = run(data, name, **args)
        assert result.status == "complete"
        assert len(result.cards[0].rows) == 21
        values = facts(result)
        assert values["q1.expense.total"] == 5400
        assert (
            sum(
                values[ref]
                for row in result.cards[0].rows
                for ref in row.fact_ids
                if ref.endswith(".total") and not ref.endswith(".previous_total")
            )
            == 5400
        )
        other = next(row for row in result.cards[0].rows if row.is_other)
        assert other.label == "其余分组" and other.group_key is None
        actual = next(row for row in result.cards[0].rows if row.group_key == "remaining")
        assert actual.label == "remaining" and not actual.is_other


@pytest.mark.parametrize(
    "numerator,denominator,expected", [(1, 3, 3333), (-1, 32, -313), (3, 1, 30000)]
)
def test_percentage_rounding_uses_signed_integer_basis_points(numerator, denominator, expected):
    assert basis_points(numerator, denominator) == expected


def test_result_contract_rejects_unknown_facts_and_wrong_call_provenance(data):
    result = run(data, "search_entries")
    value = result.model_dump()
    value["fact_refs"][0]["source_call_id"] = "other_call"
    with pytest.raises(ValueError, match="当前调用"):
        ToolResult.model_validate(value)
    value = result.model_dump()
    value["cards"][0]["rows"][0]["entry_id"] = None
    with pytest.raises(ValueError, match="账单行"):
        ToolResult.model_validate(value)
    value = result.model_dump()
    value["cards"][0]["fact_ids"].append("q1.fake")
    with pytest.raises(ValueError, match="不存在的事实"):
        ToolResult.model_validate(value)


def test_book_and_account_isolation_and_former_creator(data):
    result = run(data, "summarize_entries", book=data.books["foreign"])
    assert result.status == "error" and not result.cards
    data.ledger = LedgerService(AsyncReader(data.session), data.users["outsider"])
    result = run(data, "search_entries")
    assert result.status == "error" and not result.cards
    data.ledger = LedgerService(AsyncReader(data.session), data.users["owner"])
    result = run(data, "summarize_entries", filters={"creatorIds": [data.users["former"]]})
    assert result.status == "complete" and facts(result)["q1.expense.total"] == 3000


def test_cancel_propagates_and_releases_read_transaction(data):
    async def exercise():
        waiting = asyncio.Event()

        async def pending(_statement):
            waiting.set()
            await asyncio.Event().wait()

        data.ledger.session.execute = pending
        rollback = AsyncMock(wraps=data.ledger.session.rollback)
        data.ledger.session.rollback = rollback
        task = asyncio.create_task(
            tools(data).execute(
                "q1",
                data.books["shared"],
                "summarize_entries",
                {"filters": {"dateRange": {"start": "2026-10-21", "end": "2026-10-27"}}},
            )
        )
        await asyncio.wait_for(waiting.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        rollback.assert_awaited_once()
        assert data.session.in_transaction() is False

    asyncio.run(exercise())
