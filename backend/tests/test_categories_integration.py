"""隔离 PostgreSQL：分类权限、历史保留、并发与真实分类查询。"""

import asyncio
import os
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from test_budget_plan_integration import local_api

from app.core.config import Settings
from app.models.ledger import BookCategory
from app.models.shared import BookMember
from app.schemas.assistant import ParseRequest
from app.schemas.chat_v2 import EntryFilter
from app.services.assistant import AssistantService
from app.services.chat_v2_tools import ChatToolsV2
from app.services.ledger import LedgerService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MOJI_RUN_INTEGRATION_TESTS") != "1", reason="需显式启用隔离分类测试"
    ),
]


def category(name="宠物", type="expense"):
    return {"id": "c_" + uuid4().hex[:20], "name": name, "type": type}


async def request(api, method, path, body=None, who=0, expected=200):
    result = await api.client.request(
        method,
        "/api/v1" + path,
        json=body,
        headers={} if who is None else {"Authorization": f"Bearer user-{who}"},
    )
    assert result.status_code == expected, result.text
    return result.json()


def test_category_permissions_history_and_entry_validation():
    asyncio.run(history_case())


async def history_case():
    async with local_api() as api:
        path = f"/books/{api.books[1]}/categories"
        body = category()
        await request(api, "POST", path, body, who=None, expected=401)
        await request(api, "POST", path, body, who=2, expected=404)
        saved = await request(api, "POST", path, body, who=1)
        assert saved["bookId"] == str(api.books[1]) and saved["version"] == 1
        assert await request(api, "POST", path, body) == saved
        await request(api, "POST", path, category(), expected=409)
        await request(api, "POST", path, category("餐饮"), expected=409)
        await request(api, "POST", path, category("  "), expected=422)
        await request(api, "POST", path, {**category(), "id": "food"}, expected=422)
        income = await request(api, "POST", path, category("宠物", "income"))
        assert income["type"] == "income"
        await request(
            api,
            "PUT",
            path + "/" + body["id"],
            {"name": "宠物", "version": 1, "type": "income"},
            expected=422,
        )
        item = path + "/" + body["id"]
        for public in ("food", "salary"):
            await request(
                api, "PUT", path + "/" + public, {"name": "改名", "version": 1}, expected=403
            )
            await request(api, "DELETE", path + "/" + public + "?version=1", expected=403)
        bill = {
            "id": str(uuid4()),
            "bookId": str(api.books[1]),
            "type": "expense",
            "categoryId": body["id"],
            "amount": 1000,
            "date": "2026-11-01",
            "account": "微信",
            "note": "",
        }
        await request(api, "POST", "/entries", {**bill, "type": "income"}, expected=422)
        await request(api, "POST", "/entries", {**bill, "bookId": str(api.books[0])}, expected=422)
        entry = await request(api, "POST", "/entries", bill, who=1)
        renamed = await request(api, "PUT", item, {"name": " 猫咪用品 ", "version": 1})
        assert renamed["name"] == "猫咪用品" and renamed["version"] == 2
        await request(api, "DELETE", item + "?version=1", expected=409)
        removed = await request(api, "DELETE", item + "?version=2", who=1)
        assert removed["isDeleted"] and removed["name"] == "猫咪用品"
        assert await request(api, "DELETE", item + "?version=2") == removed
        await request(api, "PUT", item, {"name": "旧编辑", "version": 2}, expected=409)
        await request(api, "POST", "/entries", {**bill, "id": str(uuid4())}, expected=422)
        # 重试原记账与编辑历史账单均可保留原分类。
        assert await request(api, "POST", "/entries", bill, who=1) == entry
        edited = await request(
            api,
            "PUT",
            "/entries/" + entry["id"],
            {k: v for k, v in {**bill, "version": 1, "amount": 1200}.items() if k != "id"},
        )
        assert edited["categoryId"] == body["id"]
        state = await request(api, "GET", "/ledger", who=1)
        assert {row["id"] for row in state["customCategories"]} == {removed["id"], income["id"]}
        assert (
            next(row for row in state["customCategories"] if row["id"] == removed["id"]) == removed
        )
        assert state["entries"][0]["amount"] == 1200
        assert (await request(api, "GET", "/ledger", who=2))["customCategories"] == []
        # 删除的分类依旧可查历史分布、明细和筛选，不能串到账本外。
        async with api.sessions() as session:
            ledger = LedgerService(session, api.users[1])
            runner = ChatToolsV2(ledger, Settings(_env_file=None), today=date(2026, 11, 16))
            filters = EntryFilter(
                date_range={"start": "2026-11-01", "end": "2026-11-16"}, category_ids=[body["id"]]
            )
            for tool in ("summarize_entries", "search_entries", "compare_periods"):
                args = {"filters": filters.model_dump(mode="json", by_alias=True)}
                if tool == "summarize_entries":
                    args["groupBy"] = "category"
                if tool == "compare_periods":
                    args["previousRange"] = {"start": "2026-10-01", "end": "2026-10-16"}
                result = await runner.execute("q1", api.books[1], tool, args)
                assert result.status == "complete"
                assert result.cards[0].rows[0].label == "猫咪用品"
            result = await runner.execute("q2", api.books[1], "get_entry", {"entryId": entry["id"]})
            assert result.cards[0].rows[0].label == "猫咪用品"
            result = await runner.execute(
                "q3", api.books[0], "summarize_entries", {"filters": args["filters"]}
            )
            assert result.status == "error"
        recreated = await request(api, "POST", path, category("猫咪用品"))
        assert recreated["id"] != body["id"]
        async with api.sessions() as session:
            await session.execute(
                update(BookMember)
                .where(BookMember.user_id == api.users[1])
                .values(status="removed")
            )
            await session.commit()
        await request(api, "POST", path, category("成员已移出"), who=1, expected=404)


def test_category_concurrent_updates_and_duplicate_names():
    asyncio.run(concurrent_case())


async def concurrent_case():
    async with local_api() as api:
        path = f"/books/{api.books[1]}/categories"
        saved = await request(api, "POST", path, category())
        responses = await asyncio.gather(
            *[
                api.client.put(
                    "/api/v1" + path + "/" + saved["id"],
                    json={"name": name, "version": 1},
                    headers={"Authorization": f"Bearer user-{who}"},
                )
                for who, name in [(0, "宠物粮食"), (1, "宠物医疗")]
            ]
        )
        assert sorted(response.status_code for response in responses) == [200, 409]
        responses = await asyncio.gather(
            *[
                api.client.post(
                    "/api/v1" + path,
                    json=category("同名分类"),
                    headers={"Authorization": f"Bearer user-{who}"},
                )
                for who in (0, 1)
            ]
        )
        assert sorted(response.status_code for response in responses) == [200, 409]
        async with api.sessions() as session:
            rows = list(await session.scalars(select(BookCategory)))
            assert len(rows) == 2


def test_assistant_receives_book_categories_and_rejects_invalid_ids():
    asyncio.run(assistant_case())


async def assistant_case():
    async with local_api() as api:
        saved = await request(api, "POST", f"/books/{api.books[0]}/categories", category())
        output = {
            "drafts": [
                {
                    "type": "expense",
                    "amount": 1000,
                    "category_id": saved["id"],
                    "date": "2026-11-01",
                    "time": None,
                    "note": "猫粮",
                    "account": None,
                }
            ],
            "clarification": None,
        }
        llm = SimpleNamespace(structured=AsyncMock(return_value=output))
        async with api.sessions() as session:
            assistant = AssistantService(
                LedgerService(session, api.users[0]),
                llm,
                SimpleNamespace(eval=AsyncMock(return_value=1)),
                Settings(_env_file=None),
            )
            response = await assistant.parse(
                ParseRequest(book_id=api.books[0], text="宠物花费十元")
            )
            assert response.drafts[0].category_id == saved["id"]
            assert saved["id"] in llm.structured.call_args.args[1]
            from app.core.errors import ServiceError

            with pytest.raises(ServiceError, match="分类已不可用"):
                await assistant.parse(ParseRequest(book_id=api.books[1], text="宠物花费十元"))
