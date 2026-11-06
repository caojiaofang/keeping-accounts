"""在实际内存 SQL 中验证上下文持久化、引用范围、并发与旧协议隔离。"""

import asyncio
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import CreateTable
from test_analysis_v2_queries import AsyncReader
from test_analysis_v2_queries import data as data

from app.core.config import Settings
from app.core.errors import ServiceError
from app.models.chat import ChatConversation, ChatTurn
from app.models.ledger import LedgerEntry
from app.models.shared import BookMember
from app.models.user import User
from app.schemas.chat_v2 import (
    AnswerPreviewV2,
    ContextPatch,
    CreateConversationV2,
    ReferenceRequest,
    SendMessageV2,
)
from app.services.chat import ChatService
from app.services.chat_v2_context_store import ContextStoreV2
from app.services.chat_v2_tools import ChatToolsV2
from app.services.ledger import LedgerService


@compiles(JSONB, "sqlite")
def sqlite_jsonb_for_tests(_type, _compiler, **_kwargs):
    return "JSON"


class StoreSession(AsyncReader):
    async def get(self, model, key):
        return self.session.get(model, key)

    def add(self, value):
        self.session.add(value)

    async def flush(self):
        self.session.flush()

    async def commit(self):
        self.session.commit()


@pytest.fixture
def context_data(data):
    with data.session.get_bind().begin() as connection:
        connection.execute(CreateTable(ChatConversation.__table__))
        connection.execute(CreateTable(ChatTurn.__table__))
    data.ledger = LedgerService(StoreSession(data.session), data.users["owner"])
    data.settings = Settings(_env_file=None)
    data.store = ContextStoreV2(data.ledger, data.settings, today=date(2026, 10, 27))
    return data


def test_model_scope_follows_account_nickname_changes(context_data):
    async def exercise():
        data = context_data
        state = await create(data)
        data.session.execute(
            update(User).where(User.id == data.users["owner"]).values(nickname="账号新昵称")
        )
        data.session.commit()
        scope = await data.store.model_scope(state.conversation_id, question="账号新昵称记了多少")
        assert scope["creatorDirectory"] == [
            {"userId": str(data.users["owner"]), "name": "账号新昵称", "status": "active"}
        ]

    asyncio.run(exercise())


async def create(data, book="shared"):
    return await data.store.create(
        CreateConversationV2(id=uuid4(), book_id=data.books[book], initial_month="2026-10")
    )


async def save(data, cid, edits=None, *, body=None, preview=None):
    draft = await data.store.revise(cid, ContextPatch.model_validate({"edits": edits or []}))
    return await data.store.save_success(
        draft,
        body or SendMessageV2(id=uuid4(), text="核对筛选"),
        preview or AnswerPreviewV2(answer="已核对本轮范围。", completeness="complete"),
    )


def test_model_scope_only_exposes_accessible_book_creators_and_current_identity(context_data):
    async def exercise():
        data = context_data
        state = await create(data)
        scope = await data.store.model_scope(state.conversation_id, question="我和所有记账人")
        assert scope["currentUserId"] == str(data.users["owner"])
        assert scope["bookKind"] == "shared"
        expected = set(
            await data.ledger.session.scalars(
                select(BookMember.user_id).where(BookMember.book_id == data.books["shared"])
            )
        )
        assert {member["userId"] for member in scope["creatorDirectory"]} == {
            str(uid) for uid in expected
        }
        assert all(
            set(member) == {"userId", "name", "status"} for member in scope["creatorDirectory"]
        )
        unrelated = await data.store.model_scope(state.conversation_id, question="最近七天支出多少")
        assert unrelated["creatorDirectory"] == [] and unrelated["currentUserId"] is None
        overview = await data.store.model_scope(state.conversation_id, question="我这个月花了多少")
        assert overview["creatorDirectory"] == [] and overview["currentUserId"] is None
        personal = await create(data, "personal")
        assert (await data.store.model_scope(personal.conversation_id))["creatorDirectory"] == []
        await data.ledger.session.execute(
            update(BookMember)
            .where(
                BookMember.book_id == data.books["shared"],
                BookMember.user_id == data.users["owner"],
            )
            .values(status="removed")
        )
        await data.ledger.session.commit()
        with pytest.raises(ServiceError) as error:
            await data.store.model_scope(state.conversation_id)
        assert error.value.status_code == 404

    asyncio.run(exercise())


def test_seven_turn_corrections_persist_and_restore(context_data):
    data = context_data

    async def exercise():
        state = await create(data)
        cid = state.conversation_id
        first = await save(
            data,
            cid,
            [
                {"field": "categoryIds", "op": "replace", "value": ["coffee"]},
                {"field": "keywords.include", "op": "replace", "value": ["奶茶"]},
            ],
        )
        second = await save(
            data, cid, [{"field": "keywords.include", "op": "add", "value": ["咖啡"]}]
        )
        assert second.context.filters.keywords.include == ["奶茶", "咖啡"]
        third = await save(
            data, cid, [{"field": "keywords.include", "op": "replace", "value": ["咖啡"]}]
        )
        assert third.context.filters.keywords.include == ["咖啡"]
        await save(data, cid, [{"field": "categoryIds", "op": "replace", "value": ["food"]}])
        fifth = await save(data, cid, [{"field": "categoryIds", "op": "clear"}])
        assert fifth.context.filters.category_ids == []
        await save(data, cid, [{"field": "keywords.include", "op": "clear"}])
        seventh = await save(
            data, cid, [{"field": "categoryIds", "op": "restore", "sourceTurnId": str(first.id)}]
        )
        assert seventh.context.filters.category_ids == ["coffee"]
        reopened = ContextStoreV2(data.ledger, data.settings, today=date(2026, 10, 27))
        loaded = await reopened.load(cid)
        assert loaded.version == 7 and loaded.context == seventh.context
        assert len(loaded.recent_turns) == 6
        prompt = await reopened.for_model(cid)
        assert prompt["recentTurns"][-1]["answer"] == seventh.answer
        assert prompt["historyIsSnapshot"] and prompt["omittedHistory"]
        assert data.session.scalar(select(func.count()).select_from(ChatTurn)) == 7

    asyncio.run(exercise())


def test_failed_partial_and_stale_drafts_do_not_replace_success(context_data):
    data = context_data

    async def exercise():
        state = await create(data)
        cid = state.conversation_id
        draft = await data.store.revise(cid, ContextPatch())
        successful = await save(
            data, cid, [{"field": "accounts", "op": "replace", "value": ["微信"]}]
        )
        with pytest.raises(ServiceError, match="其他请求"):
            await data.store.save_success(
                draft,
                SendMessageV2(id=uuid4(), text="迟到消息"),
                AnswerPreviewV2(answer="迟到结果", completeness="complete"),
            )
        newer = await data.store.revise(
            cid,
            ContextPatch.model_validate(
                {"edits": [{"field": "accounts", "op": "replace", "value": ["支付宝"]}]}
            ),
        )
        with pytest.raises(ServiceError, match="未完成"):
            await data.store.save_success(
                newer,
                SendMessageV2(id=uuid4(), text="失败轮次"),
                AnswerPreviewV2(
                    answer="只有部分结果", completeness="partial", missing=["查询未完成"]
                ),
            )
        assert (await data.store.load(cid)).context == successful.context
        assert data.session.scalar(select(func.count()).select_from(ChatTurn)) == 1

    asyncio.run(exercise())


def test_repeated_message_is_idempotent_and_changed_content_is_rejected(context_data):
    data = context_data

    async def exercise():
        state = await create(data)
        draft = await data.store.revise(state.conversation_id, ContextPatch())
        body = SendMessageV2(id=uuid4(), text="第一条消息")
        preview = AnswerPreviewV2(answer="原回答", completeness="complete")
        original = await data.store.save_success(draft, body, preview)
        assert await data.store.save_success(draft, body, preview) == original
        with pytest.raises(ServiceError, match="已处理"):
            await data.store.save_success(
                draft, SendMessageV2(id=body.id, text="修改内容"), preview
            )
        with pytest.raises(ServiceError, match="已处理"):
            await data.store.save_success(
                draft, body.model_copy(update={"suggestion_id": uuid4()}), preview
            )
        assert (await data.store.load(state.conversation_id)).version == 1

    asyncio.run(exercise())


def test_failure_after_version_update_rolls_back_whole_turn(context_data, monkeypatch):
    data = context_data

    async def exercise():
        state = await create(data)
        original = await save(data, state.conversation_id)
        draft = await data.store.revise(
            state.conversation_id,
            ContextPatch.model_validate(
                {"edits": [{"field": "accounts", "op": "replace", "value": ["支付宝"]}]}
            ),
        )

        async def fail_flush():
            raise RuntimeError("模拟保存中断")

        monkeypatch.setattr(data.ledger.session, "flush", fail_flush)
        with pytest.raises(RuntimeError, match="保存中断"):
            await data.store.save_success(
                draft,
                SendMessageV2(id=uuid4(), text="未保存的修正"),
                AnswerPreviewV2(answer="未保存的回答", completeness="complete"),
            )
        loaded = await data.store.load(state.conversation_id)
        assert loaded.version == 1 and loaded.context == original.context
        assert data.session.scalar(select(func.count()).select_from(ChatTurn)) == 1

    asyncio.run(exercise())


def test_account_book_and_source_turn_isolation(context_data):
    data = context_data

    async def exercise():
        one, two = await create(data), await create(data, "personal")
        first = await save(data, one.conversation_id)
        with pytest.raises(ServiceError, match="来源轮次"):
            await data.store.revise(
                two.conversation_id,
                ContextPatch.model_validate(
                    {
                        "edits": [
                            {"field": "categoryIds", "op": "restore", "sourceTurnId": str(first.id)}
                        ]
                    }
                ),
            )
        foreign = ContextStoreV2(
            LedgerService(StoreSession(data.session), data.users["member"]),
            data.settings,
            today=date(2026, 10, 27),
        )
        with pytest.raises(ServiceError):
            await foreign.load(one.conversation_id)
        with pytest.raises(ServiceError):
            await foreign.resolve_reference(one.conversation_id, ReferenceRequest())

    asyncio.run(exercise())


async def save_search(data, cid):
    draft = await data.store.revise(
        cid,
        ContextPatch.model_validate(
            {
                "edits": [
                    {
                        "field": "dateRange",
                        "op": "replace",
                        "value": {"start": "2026-10-21", "end": "2026-10-27"},
                    },
                    {"field": "keywords.include", "op": "replace", "value": ["奶茶"]},
                ]
            }
        ),
    )
    result = await ChatToolsV2(data.ledger, data.settings, today=date(2026, 10, 27)).execute(
        "q1",
        data.books["shared"],
        "search_entries",
        {"filters": draft.context.filters.model_dump()},
    )
    return await data.store.save_success(
        draft,
        SendMessageV2(id=uuid4(), text="查询奶茶"),
        AnswerPreviewV2(
            answer="原回答快照",
            completeness="complete",
            cards=result.cards,
            fact_refs=result.fact_refs,
        ),
    )


def test_reference_ambiguity_explicit_selection_and_live_refresh(context_data):
    data = context_data

    async def exercise():
        state = await create(data)
        turn = await save_search(data, state.conversation_id)
        ambiguous = await data.store.resolve_reference(state.conversation_id, ReferenceRequest())
        assert ambiguous.status == "clarify" and len(ambiguous.candidates) == 3
        multiple = await data.store.resolve_reference(
            state.conversation_id, ReferenceRequest(kind="entries")
        )
        assert multiple.status == "resolved" and len(multiple.candidates) == 3
        data.session.execute(
            update(LedgerEntry)
            .where(LedgerEntry.id == data.ids["milk"])
            .values(amount=5000, note="更新后的奶茶")
        )
        data.session.commit()
        selected = await data.store.resolve_reference(
            state.conversation_id,
            ReferenceRequest(
                turn_id=turn.id, card_id=turn.cards[0].id, entry_ids=[data.ids["milk"]]
            ),
        )
        assert selected.status == "resolved" and selected.candidates[0].label == "更新后的奶茶"
        fresh = await ChatToolsV2(data.ledger, data.settings, today=date(2026, 10, 27)).execute(
            "q2", data.books["shared"], "get_entry", {"entryId": str(data.ids["milk"])}
        )
        assert next(f.value for f in fresh.fact_refs if f.id == "q2.expense.total") == 5000
        old = (await data.store.load(state.conversation_id)).recent_turns[0]
        assert (
            next(
                f.value
                for f in old.fact_refs
                if f.id.endswith("e" + data.ids["milk"].hex + ".amount")
            )
            == 1000
        )
        with pytest.raises(ServiceError, match="已展示结果"):
            await data.store.resolve_reference(
                state.conversation_id,
                ReferenceRequest(turn_id=turn.id, entry_ids=[data.ids["literal"]]),
            )

    asyncio.run(exercise())


def test_deleted_reference_and_removed_member_do_not_restore_old_data(context_data):
    data = context_data

    async def exercise():
        state = await create(data)
        await save_search(data, state.conversation_id)
        data.session.execute(
            update(LedgerEntry).where(LedgerEntry.id == data.ids["milk"]).values(is_deleted=True)
        )
        data.session.commit()
        result = await data.store.resolve_reference(
            state.conversation_id, ReferenceRequest(kind="entries")
        )
        assert result.status == "clarify" and len(result.candidates) == 2
        data.session.execute(
            update(BookMember)
            .where(
                BookMember.book_id == data.books["shared"],
                BookMember.user_id == data.users["owner"],
            )
            .values(status="removed")
        )
        data.session.commit()
        with pytest.raises(ServiceError):
            await data.store.load(state.conversation_id)

    asyncio.run(exercise())


def test_legacy_readonly_fork_and_v1_v2_history_separation(context_data):
    data = context_data

    async def exercise():
        legacy_id = uuid4()
        data.session.add(
            ChatConversation(
                id=legacy_id,
                owner_id=data.users["owner"],
                book_id=data.books["shared"],
                month="2026-10",
                version=1,
                context={"month": "2026-09", "categoryId": "food", "entryType": "expense"},
            )
        )
        data.session.add(
            ChatTurn(
                id=uuid4(),
                conversation_id=legacy_id,
                sequence=1,
                text="旧问题",
                answer="旧回答",
                context={"month": "2026-09", "categoryId": "food", "entryType": "expense"},
            )
        )
        data.session.commit()
        with pytest.raises(ServiceError, match="仅支持读取"):
            await data.store.load(legacy_id)
        old = await data.store.load(legacy_id, allow_legacy=True)
        assert old.protocol == 1 and old.context.filters.category_ids == ["food"]
        assert old.recent_turns[0].answer == "旧回答" and not old.recent_turns[0].cards
        body = CreateConversationV2(
            id=uuid4(), book_id=data.books["shared"], initial_month="2026-10"
        )
        fork = await data.store.fork_legacy(legacy_id, body)
        assert fork.protocol == 2 and fork.context.filters.date_range.start == date(2026, 9, 1)
        await save(data, fork.conversation_id)
        legacy = ChatService(data.ledger, None, None, data.settings)
        history = await legacy.list(data.books["shared"], "2026-10")
        assert [item.id for item in history] == [legacy_id]
        with pytest.raises(ServiceError):
            await legacy.require_conversation(fork.conversation_id)
        assert (await data.store.load(legacy_id, allow_legacy=True)).version == 1

    asyncio.run(exercise())
