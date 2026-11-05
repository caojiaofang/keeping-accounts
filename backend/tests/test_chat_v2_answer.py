"""从模拟模型、真实内存 SQL 走到可信回答与保存建议，不调用远程服务。"""

import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select, update
from test_analysis_v2_queries import data as data
from test_chat_v2_context_store import StoreSession, create
from test_chat_v2_context_store import context_data as context_data
from test_chat_v2_loop import ANSWER, FILTERS, PLAN, Script, configured, tool

from app.core.errors import ServiceError
from app.integrations.chat_v2_llm import ChatModelV2
from app.integrations.llm import LlmClient
from app.models.chat import ChatTurn
from app.models.ledger import LedgerEntry
from app.models.shared import BookMember
from app.schemas.chat_v2 import AnswerPreviewV2, ContextPatch, FactRef, SendMessageV2, ToolResult
from app.services.chat_v2_answer import (
    AnswerServiceV2,
    collect_results,
    format_fact,
    render_fact_template,
)
from app.services.chat_v2_context_store import ContextStoreV2
from app.services.chat_v2_loop import ChatLoopV2
from app.services.chat_v2_suggestions import (
    CATEGORIES,
    COMPARE,
    CONTRIBUTION,
    DETAILS,
    ENTRY,
    EXCLUDE,
    WIDEN,
)
from app.services.ledger import LedgerService

SCOPED_PLAN = {
    **PLAN,
    "patch": {
        "edits": [
            {
                "field": "dateRange",
                "op": "replace",
                "value": FILTERS["dateRange"],
            }
        ]
    },
}


def test_primary_query_cannot_save_context_with_different_keywords(context_data):
    async def exercise():
        with pytest.raises(ServiceError, match="主统计与讨论筛选不一致") as error:
            await prepare(
                context_data,
                [
                    SCOPED_PLAN,
                    tool(filters={**FILTERS, "keywords": {"include": ["奶茶", "咖啡"]}}),
                    ANSWER,
                ],
            )
        assert error.value.status_code == 409
        assert (
            await context_data.ledger.session.scalar(select(func.count()).select_from(ChatTurn))
            == 0
        )

    asyncio.run(exercise())


def test_cleared_keywords_are_authoritative_without_conflicting_old_context(context_data):
    async def exercise():
        data = context_data
        state = await create(data)
        draft = await data.store.revise(
            state.conversation_id,
            ContextPatch.model_validate(
                {
                    "edits": [
                        {"field": "keywords.include", "op": "replace", "value": ["奶茶", "咖啡"]},
                        {"field": "keywords.exclude", "op": "replace", "value": ["退款"]},
                    ]
                }
            ),
        )
        await data.store.save_success(
            draft,
            SendMessageV2(id=uuid4(), text="只看奶茶和咖啡，排除退款"),
            AnswerPreviewV2(answer="已保存旧筛选快照。", completeness="complete"),
        )
        plan = {
            **SCOPED_PLAN,
            "patch": {
                "edits": [
                    *SCOPED_PLAN["patch"]["edits"],
                    {"field": "keywords.include", "op": "clear"},
                    {"field": "keywords.exclude", "op": "clear"},
                ]
            },
        }
        ready, _, script = await prepare(
            data, [plan, tool(), ANSWER], cid=state.conversation_id, text="清空备注关键词"
        )
        sent = json.loads(script.requests[1]["input"][1]["content"])
        assert "context" not in sent["history"]
        assert sent["history"]["recentTurns"]
        assert sent["currentContext"]["filters"]["keywords"] == {
            "include": [],
            "exclude": [],
            "mode": "any",
        }
        assert ready.preview.completeness == "complete"

    asyncio.run(exercise())


async def prepare(
    data, steps=None, *, text="最近七天花了多少", mode="responses", tools="native", cid=None
):
    state = await create(data) if cid is None else await data.store.load(cid)
    settings = configured(data, mode=mode, tools=tools)
    data.store.settings = settings
    body = SendMessageV2(id=uuid4(), text=text)
    script = Script(settings, steps or [SCOPED_PLAN, tool(), ANSWER])
    async with httpx.AsyncClient(transport=httpx.MockTransport(script)) as client:
        loop = ChatLoopV2(data.store, ChatModelV2(LlmClient(settings, client)))
        result = await AnswerServiceV2(loop).run(state.conversation_id, body)
    return result, body, script


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("tools", ["native", "structured"])
def test_direct_answer_without_redundant_card_or_extra_model_request(context_data, mode, tools):
    async def exercise():
        ready, body, script = await prepare(context_data, mode=mode, tools=tools)
        preview = ready.preview
        assert preview.completeness == "complete" and not preview.missing and not preview.cards
        assert "¥475.01" in preview.answer and "6 笔" in preview.answer
        assert "2026-10-21 至 2026-10-27" in preview.answer and "{{" not in preview.answer
        assert 2 <= len(preview.suggestions) <= 3
        assert {s.text for s in preview.suggestions} == {DETAILS, CATEGORIES, COMPARE}
        assert all(s.source_turn_id == body.id and s.reference is None for s in preview.suggestions)
        assert len(script.requests) == ready.model_requests == 3
        assert (await context_data.store.load(ready.draft.conversation_id)).version == 0
        saved = await context_data.store.save_success(ready.draft, body, preview)
        assert saved.suggestions == preview.suggestions

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "text",
    [
        "花了 475.01 元",
        "共６笔",
        "增加了百分之三十",
        "收入两万元",
        "增长20％",
        "金额³元",
        "{{q9.expense.total}}",
        "{{q1.unknown}}",
        "{{broken",
        "{{q1.expense.total}}再省五百元",
    ],
)
def test_unverified_numbers_and_unknown_refs_degrade_to_backend_facts(context_data, text):
    async def exercise():
        ready, _, _ = await prepare(
            context_data, [SCOPED_PLAN, tool(), {"action": "answer", "text": text}]
        )
        assert ready.preview.completeness == "partial" and ready.preview.missing
        assert "¥475.01" in ready.preview.answer and "已验证" in ready.preview.answer
        assert text not in ready.preview.answer and "{{" not in ready.preview.answer
        with pytest.raises(ServiceError, match="未完成"):
            await context_data.store.save_success(
                ready.draft, SendMessageV2(id=uuid4(), text="不能保存"), ready.preview
            )

    asyncio.run(exercise())


def test_model_events_and_saving_guarantees_are_not_published(context_data):
    async def exercise():
        proposal = "因为你失恋后报复性消费，按方案一定能省钱。本期为 {{q1.expense.total}}。"
        ready, _, _ = await prepare(
            context_data, [SCOPED_PLAN, tool(), {"action": "answer", "text": proposal}]
        )
        assert ready.preview.completeness == "complete"
        assert all(word not in ready.preview.answer for word in ("失恋", "报复", "一定能", "方案"))
        assert "¥475.01" in ready.preview.answer

    asyncio.run(exercise())


def test_comparison_reports_negative_contribution_without_inventing_causes(context_data):
    async def exercise():
        context_data.session.execute(
            update(LedgerEntry)
            .where(LedgerEntry.id == context_data.ids["previous"])
            .values(
                category_id="food",
                amount=10000,
            )
        )
        context_data.session.commit()
        ready, _, _ = await prepare(
            context_data,
            [
                SCOPED_PLAN,
                tool(
                    "compare_periods",
                    filters=FILTERS,
                    previousRange={"start": "2026-10-14", "end": "2026-10-20"},
                ),
                {"action": "answer", "text": "差额 {{q1.expense.difference}}。"},
            ],
            text="为什么多花了",
        )
        assert ready.preview.cards[0].type == "comparison"
        assert "餐饮 −¥70.00" in ready.preview.answer
        assert "不能证明实际消费原因" in ready.preview.answer
        assert "2026-10-14 至 2026-10-20" in ready.preview.answer
        assert ready.draft.context.previous_range.start.isoformat() == "2026-10-14"
        assert CONTRIBUTION in {s.text for s in ready.preview.suggestions}

    asyncio.run(exercise())


@pytest.mark.parametrize("compare", [False, True])
def test_zero_records_do_not_produce_causes_or_savings_and_zero_base_has_no_rate(
    context_data, compare
):
    async def exercise():
        filters = {**FILTERS, "keywords": {"include": ["没有匹配"]}}
        action = (
            tool(
                "compare_periods",
                filters=filters,
                previousRange={"start": "2026-10-01", "end": "2026-10-07"},
            )
            if compare
            else tool(filters=filters)
        )
        ready, _, _ = await prepare(
            context_data,
            [
                {
                    **SCOPED_PLAN,
                    "patch": {
                        "edits": [
                            *SCOPED_PLAN["patch"]["edits"],
                            {"field": "keywords.include", "op": "replace", "value": ["没有匹配"]},
                        ]
                    },
                },
                action,
                ANSWER,
            ],
            text="比较花费" if compare else "花多少",
        )
        assert "没有支出记录" in ready.preview.answer
        assert all(word not in ready.preview.answer for word in ("节省", "消费原因是", "%"))
        if compare:
            assert "上期没有匹配记录" in ready.preview.answer
            assert not any(f.metric == "change_rate" for f in ready.preview.fact_refs)
        assert WIDEN in {s.text for s in ready.preview.suggestions}

    asyncio.run(exercise())


def test_detail_query_keeps_real_entry_cards_and_single_entry_suggestion(context_data):
    async def exercise():
        ready, body, _ = await prepare(
            context_data,
            [
                SCOPED_PLAN,
                tool("get_entry", entryId=str(context_data.ids["milk"])),
                ANSWER,
            ],
            text="这笔奶茶是哪笔",
        )
        preview = ready.preview
        assert preview.cards[0].rows[0].entry_id == context_data.ids["milk"]
        assert "¥10.00" in preview.answer
        suggestion = next(s for s in preview.suggestions if s.text == ENTRY)
        assert suggestion.reference.entry_ids == [context_data.ids["milk"]]
        assert suggestion.reference.turn_id == body.id
        await context_data.store.save_success(ready.draft, body, preview)
        plan = await context_data.store.resolve_suggestion(
            ready.draft.conversation_id,
            SendMessageV2(id=uuid4(), text=suggestion.text, suggestion_id=suggestion.id),
        )
        assert plan.reference.entry_ids == [context_data.ids["milk"]]

    asyncio.run(exercise())


def test_partial_loop_keeps_missing_and_only_verified_cards(context_data):
    async def exercise():
        ready, _, _ = await prepare(
            context_data,
            [
                SCOPED_PLAN,
                tool(),
                httpx.Response(503, json={"error": "private"}),
            ],
        )
        assert ready.preview.completeness == "partial" and ready.preview.missing
        assert "¥475.01" in ready.preview.answer and "private" not in ready.preview.answer

    asyncio.run(exercise())


def test_saved_followup_restores_its_scope_and_skips_model_planning(context_data):
    async def exercise():
        data = context_data
        ready, body, _ = await prepare(data)
        await data.store.save_success(ready.draft, body, ready.preview)
        suggestion = next(s for s in ready.preview.suggestions if s.text == COMPARE)
        cid = ready.draft.conversation_id
        other = await data.store.revise(
            cid,
            ContextPatch.model_validate(
                {
                    "edits": [
                        {
                            "field": "dateRange",
                            "op": "replace",
                            "value": {"start": "2026-10-01", "end": "2026-10-02"},
                        }
                    ]
                }
            ),
        )
        await data.store.save_success(
            other,
            SendMessageV2(id=uuid4(), text="换日期"),
            ready.preview.model_copy(update={"suggestions": []}),
        )
        settings = configured(data)
        script = Script(
            settings,
            [
                tool(
                    "compare_periods",
                    filters=FILTERS,
                    previousRange={"start": "2026-10-14", "end": "2026-10-20"},
                ),
                {"action": "answer", "text": "差额 {{q1.expense.difference}}。"},
            ],
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(script)) as client:
            loop = ChatLoopV2(data.store, ChatModelV2(LlmClient(settings, client)))
            next_body = SendMessageV2(id=uuid4(), text=suggestion.text, suggestion_id=suggestion.id)
            next_ready = await AnswerServiceV2(loop).run(cid, next_body)
        assert next_ready.model_requests == len(script.requests) == 2
        assert next_ready.draft.context.filters.date_range.start.isoformat() == "2026-10-21"
        assert next_ready.draft.context.previous_range.start.isoformat() == "2026-10-14"

    asyncio.run(exercise())


def test_changed_suggestion_text_foreign_id_and_foreign_account_are_rejected(context_data):
    async def exercise():
        data = context_data
        ready, body, _ = await prepare(data)
        await data.store.save_success(ready.draft, body, ready.preview)
        suggestion = ready.preview.suggestions[0]
        cid = ready.draft.conversation_id
        with pytest.raises(ServiceError, match="不一致"):
            await data.store.resolve_suggestion(
                cid, SendMessageV2(id=uuid4(), text="篡改", suggestion_id=suggestion.id)
            )
        other = await create(data, "personal")
        with pytest.raises(ServiceError, match="不存在或无权访问"):
            await data.store.resolve_suggestion(
                other.conversation_id,
                SendMessageV2(id=uuid4(), text=suggestion.text, suggestion_id=suggestion.id),
            )
        foreign = ContextStoreV2(
            LedgerService(StoreSession(data.session), data.users["member"]),
            data.settings,
            today=data.store.today,
        )
        with pytest.raises(ServiceError):
            await foreign.resolve_suggestion(
                cid, SendMessageV2(id=uuid4(), text=suggestion.text, suggestion_id=suggestion.id)
            )

    asyncio.run(exercise())


def test_deleted_entry_or_revoked_book_cannot_be_used_by_suggestion(context_data):
    async def exercise():
        data = context_data
        ready, body, _ = await prepare(
            data,
            [SCOPED_PLAN, tool("get_entry", entryId=str(data.ids["milk"])), ANSWER],
            text="这笔明细",
        )
        await data.store.save_success(ready.draft, body, ready.preview)
        suggestion = next(s for s in ready.preview.suggestions if s.text == ENTRY)
        request = SendMessageV2(id=uuid4(), text=suggestion.text, suggestion_id=suggestion.id)
        data.session.execute(
            update(LedgerEntry).where(LedgerEntry.id == data.ids["milk"]).values(is_deleted=True)
        )
        data.session.commit()
        with pytest.raises(ServiceError, match="已失效或无权访问"):
            await data.store.resolve_suggestion(ready.draft.conversation_id, request)
        data.session.execute(
            update(BookMember)
            .where(
                BookMember.book_id == data.books["shared"],
                BookMember.user_id == data.users["owner"],
            )
            .values(status="removed")
        )
        data.session.commit()
        with pytest.raises(ServiceError, match="不存在或无权访问"):
            await data.store.resolve_suggestion(ready.draft.conversation_id, request)

    asyncio.run(exercise())


def test_saving_forged_source_or_bill_reference_is_rejected(context_data):
    async def exercise():
        ready, body, _ = await prepare(context_data)
        bad = ready.preview.model_copy(deep=True)
        bad.suggestions[0].source_turn_id = uuid4()
        with pytest.raises(ServiceError, match="建议来源"):
            await context_data.store.save_success(ready.draft, body, bad)
        assert (await context_data.store.load(ready.draft.conversation_id)).version == 0
        assert context_data.session.scalar(select(func.count()).select_from(ChatTurn)) == 0

    asyncio.run(exercise())


def test_answer_reuses_deadline_and_cannot_be_rebound_to_another_message(context_data):
    async def exercise():
        data = context_data
        state = await create(data)
        settings = configured(data)
        script = Script(settings, [SCOPED_PLAN, tool(), ANSWER])
        body = SendMessageV2(id=uuid4(), text="最近七天花多少")
        async with httpx.AsyncClient(transport=httpx.MockTransport(script)) as client:
            loop = ChatLoopV2(data.store, ChatModelV2(LlmClient(settings, client)))
            outcome = await loop.run(state.conversation_id, body)
            service = AnswerServiceV2(loop)
            with pytest.raises(ServiceError, match="本轮消息不一致"):
                await service.prepare(outcome, body.model_copy(update={"id": uuid4()}))
            with pytest.raises(ServiceError, match="本轮分析超时"):
                await service.prepare(
                    replace(outcome, deadline=asyncio.get_running_loop().time() - 1), body
                )
        assert len(script.requests) == 3

    asyncio.run(exercise())


def test_changed_totals_across_tools_are_not_combined_as_one_conclusion(context_data):
    async def change(_payload):
        context_data.session.execute(
            update(LedgerEntry)
            .where(LedgerEntry.id == context_data.ids["milk"])
            .values(amount=9000)
        )
        context_data.session.commit()
        return tool("summarize_entries", filters=FILTERS, groupBy="category")

    async def exercise():
        with pytest.raises(ServiceError, match="账单在分析期间已变化"):
            await prepare(context_data, [SCOPED_PLAN, tool(), change, ANSWER])

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "value,unit,expected",
    [
        (12345678901, "cents", "¥123,456,789.01"),
        (-101, "cents", "−¥1.01"),
        (0, "count", "0 笔"),
        (-1234, "basis_points", "-12.34%"),
        ("2026-10-27", "date", "2026-10-27"),
    ],
)
def test_fact_formatting_uses_integer_units_and_preserves_negative_values(value, unit, expected):
    fact = FactRef(id="q1.x", source_call_id="q1", value=value, unit=unit, label="测试事实")
    assert format_fact(fact) == expected
    assert render_fact_template("已验证：{{q1.x}}。", {fact.id: fact}) == f"已验证：{expected}。"


def test_free_text_fact_cannot_smuggle_unverified_numbers():
    fact = FactRef(
        id="q1.label", source_call_id="q1", value="保证省500元", unit="text", label="不可信标签"
    )
    with pytest.raises(ServiceError, match="未经验证的数值"):
        render_fact_template("{{q1.label}}", {fact.id: fact})


def test_numeric_fact_with_wrong_metric_unit_is_rejected():
    result = ToolResult(
        call_id="q1",
        name="summarize_entries",
        status="complete",
        fact_refs=[
            FactRef(
                id="q1.expense.total",
                source_call_id="q1",
                metric="total",
                unit="count",
                value=1000,
                label="错误单位",
            ),
        ],
    )
    with pytest.raises(ServiceError, match="指标与单位不一致"):
        collect_results([result])


@pytest.mark.parametrize("name", ["summarize_entries", "search_entries"])
def test_income_and_expense_are_reported_separately_even_in_one_entries_card(context_data, name):
    async def exercise():
        filters = {**FILTERS, "entryType": "both"}
        ready, _, _ = await prepare(
            context_data,
            [
                {
                    **SCOPED_PLAN,
                    "patch": {
                        "edits": [
                            *SCOPED_PLAN["patch"]["edits"],
                            {"field": "entryType", "op": "replace", "value": "both"},
                        ]
                    },
                },
                tool(name, filters=filters),
                ANSWER,
            ],
        )
        assert "支出合计 ¥475.01" in ready.preview.answer
        assert "收入合计 ¥500.00" in ready.preview.answer
        assert "¥975.01" not in ready.preview.answer

    asyncio.run(exercise())


def test_exclude_and_compare_followup_preserves_original_comparison_scope(context_data):
    async def exercise():
        data = context_data
        ready, body, _ = await prepare(
            data,
            [
                SCOPED_PLAN,
                tool(
                    "compare_periods",
                    filters=FILTERS,
                    previousRange={"start": "2026-10-14", "end": "2026-10-20"},
                ),
                tool("get_entry", entryId=str(data.ids["milk"])),
                {"action": "answer", "text": "差额 {{q1.expense.difference}}。"},
            ],
            text="为什么多花了",
        )
        suggestion = next(s for s in ready.preview.suggestions if s.text == EXCLUDE)
        await data.store.save_success(ready.draft, body, ready.preview)
        request = SendMessageV2(id=uuid4(), text=suggestion.text, suggestion_id=suggestion.id)
        filters = {**FILTERS, "excludedEntryIds": [str(data.ids["milk"])]}
        settings = configured(data)
        script = Script(
            settings,
            [
                tool(
                    "compare_periods",
                    filters=filters,
                    previousRange={"start": "2026-10-14", "end": "2026-10-20"},
                ),
                {"action": "answer", "text": "差额 {{q1.expense.difference}}。"},
            ],
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(script)) as client:
            loop = ChatLoopV2(data.store, ChatModelV2(LlmClient(settings, client)))
            result = await AnswerServiceV2(loop).run(ready.draft.conversation_id, request)
        assert result.draft.context.filters.excluded_entry_ids == [data.ids["milk"]]
        assert result.draft.context.filters.date_range.start.isoformat() == "2026-10-21"
        assert result.draft.context.previous_range.start.isoformat() == "2026-10-14"
        assert result.model_requests == 2 and "¥465.01" in result.preview.answer

    asyncio.run(exercise())


def test_unrelated_deleted_old_entry_does_not_block_generic_followup(context_data):
    async def exercise():
        data = context_data
        ready, body, _ = await prepare(
            data, [SCOPED_PLAN, tool("search_entries", filters=FILTERS), ANSWER], text="查看明细"
        )
        await data.store.save_success(ready.draft, body, ready.preview)
        generic = next(s for s in ready.preview.suggestions if s.text == CATEGORIES)
        data.session.execute(
            update(LedgerEntry).where(LedgerEntry.id == data.ids["milk"]).values(is_deleted=True)
        )
        data.session.commit()
        plan = await data.store.resolve_suggestion(
            ready.draft.conversation_id,
            SendMessageV2(id=uuid4(), text=generic.text, suggestion_id=generic.id),
        )
        assert plan.reference is None and not any(
            e.field == "excludedEntryIds" and e.value for e in plan.patch.edits
        )

    asyncio.run(exercise())
