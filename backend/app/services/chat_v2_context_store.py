"""按账号和账本保存 V2 成功轮次，复用现有 JSONB 字段并隔离旧协议。"""

import re
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime

from pydantic import ValidationError
from sqlalchemy import String, func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.core.errors import ServiceError
from app.models.chat import ChatConversation, ChatTurn
from app.models.ledger import Book, LedgerEntry
from app.schemas.chat_v2 import (
    AnswerPreviewV2,
    ChatContextV2,
    ContextPatch,
    ConversationResponseV2,
    ConversationSummaryV2,
    CreateConversationV2,
    LegacyConversationResponseV2,
    ReferenceCandidate,
    ReferenceRequest,
    ReferenceResolution,
    ResultCard,
    ResultReference,
    SendMessageV2,
    TurnResponseV2,
)
from app.services.analysis import CATEGORIES
from app.services.analysis_v2 import FilterQueriesV2, validate_date_range
from app.services.chat_v2_context import (
    ContextDraft,
    ContextState,
    apply_context_patch,
    completed_context,
    convert_legacy_context,
    initial_context,
    model_context,
)
from app.services.chat_v2_suggestions import (
    COMPARE,
    CONTRIBUTION,
    ENTRY,
    EXCLUDE,
    TEXTS,
    category_target,
    suggestion_plan,
)
from app.services.ledger import LedgerService


def _timestamp(value):
    return int((value if value.tzinfo else value.replace(tzinfo=UTC)).timestamp() * 1000)


def _summary(row):
    return ConversationSummaryV2(
        id=row.id,
        book_id=row.book_id,
        initial_month=row.month,
        title=row.title,
        updated_at=_timestamp(row.updated_at),
    )


class ContextStoreV2:
    def __init__(self, ledger: LedgerService, settings: Settings, *, today: date):
        self.ledger, self.settings, self.today = ledger, settings, today
        self.filters = FilterQueriesV2(ledger, settings, today=today)

    async def _require(self, conversation_id):
        conversation = await self.ledger.session.scalar(
            select(ChatConversation)
            .join(Book)
            .where(
                ChatConversation.id == conversation_id,
                ChatConversation.owner_id == self.ledger.user_id,
                self.ledger.access_condition(),
            )
            .execution_options(populate_existing=True)
        )
        if conversation is None:
            raise ServiceError(404, "会话不存在或无权访问")
        await self.ledger.require_book(conversation.book_id)
        return conversation

    @asynccontextmanager
    async def _access(self, conversation_id, *, allow_legacy=False):
        try:
            conversation = await self._require(conversation_id)
            if not allow_legacy and (conversation.context or {}).get("schemaVersion") != 2:
                raise ServiceError(409, "旧会话仅支持读取，请开始新的 V2 会话")
            yield conversation
        finally:
            await self.ledger.session.rollback()

    def _context(self, values, month):
        if not isinstance(values, dict) or values.get("schemaVersion") not in {None, 1, 2}:
            raise ServiceError(409, "会话版本暂不支持，请开始新对话")
        try:
            if values.get("schemaVersion") == 2:
                return ChatContextV2.model_validate(values)
            return convert_legacy_context(
                values,
                initial_month=month,
                today=self.today,
                max_days=self.settings.chat_v2_max_range_days,
            )
        except ValidationError:
            raise ServiceError(409, "会话上下文不完整，请重新查询或开始新对话") from None

    def _turn(self, row, month, *, legacy=False):
        if legacy:
            preview = AnswerPreviewV2(answer=row.answer, completeness="complete")
        else:
            if not row.result or row.result.get("schemaVersion") != 2:
                raise ServiceError(409, "历史回答结构不完整，请开始新对话")
            preview = AnswerPreviewV2.model_validate(row.result["payload"])
        return TurnResponseV2(
            **preview.model_dump(),
            id=row.id,
            sequence=row.sequence,
            text=row.text,
            context=self._context(row.context, month),
            created_at=_timestamp(row.created_at),
        )

    async def create(self, body: CreateConversationV2, *, context=None):
        body = CreateConversationV2.model_validate(body.model_dump())
        values = context or initial_context(
            body.initial_month, today=self.today, max_days=self.settings.chat_v2_max_range_days
        )
        values = ChatContextV2(
            filters=values.filters, previous_range=values.previous_range, sources=values.sources
        )
        try:
            await self.ledger.require_book(body.book_id)
            existing = await self.ledger.session.get(ChatConversation, body.id)
            if existing:
                if existing.owner_id != self.ledger.user_id or existing.book_id != body.book_id:
                    raise ServiceError(404, "会话不存在或无权访问")
                if (existing.context or {}).get(
                    "schemaVersion"
                ) != 2 or existing.month != body.initial_month:
                    raise ServiceError(409, "会话编号已使用，请选择新编号")
            else:
                prepared = await self.filters.prepare(body.book_id, values.filters)
                values = ChatContextV2.model_validate(
                    {**values.model_dump(), "filters": prepared.filters}
                )
                self.ledger.session.add(
                    ChatConversation(
                        id=body.id,
                        owner_id=self.ledger.user_id,
                        book_id=body.book_id,
                        month=body.initial_month,
                        title="新的消费分析",
                        context=values.model_dump(mode="json", by_alias=True),
                        version=0,
                    )
                )
                await self.ledger.session.flush()
                await self.ledger.session.commit()
        except IntegrityError:
            raise ServiceError(409, "会话已由其他请求创建，请重新读取") from None
        finally:
            await self.ledger.session.rollback()
        return await self.load(body.id)

    async def load(self, conversation_id, *, allow_legacy=False):
        async with self._access(conversation_id, allow_legacy=allow_legacy) as conversation:
            protocol = 2 if (conversation.context or {}).get("schemaVersion") == 2 else 1
            context = self._context(conversation.context or {}, conversation.month)
            rows = list(
                await self.ledger.session.scalars(
                    select(ChatTurn)
                    .where(
                        ChatTurn.conversation_id == conversation_id,
                    )
                    .order_by(ChatTurn.sequence.desc())
                    .limit(self.settings.chat_v2_context_recent_turns)
                )
            )
            recent = tuple(
                self._turn(row, conversation.month, legacy=protocol == 1) for row in reversed(rows)
            )
            await self.ledger.require_book(conversation.book_id)
            return ContextState(
                conversation.id,
                self.ledger.user_id,
                conversation.book_id,
                conversation.month,
                conversation.version,
                protocol,
                context,
                recent,
            )

    async def for_model(self, conversation_id):
        return model_context(await self.load(conversation_id), self.settings)

    async def model_scope(self, conversation_id, *, question=""):
        """只提供当前可访问账本的记账人目录；姓名是数据，不能据此切换身份。"""
        async with self._access(conversation_id) as conversation:
            book = await self.ledger.require_book(conversation.book_id)
            members = []
            if book.kind == "shared":
                members = await self.ledger.member_directory(book.id, limit=51)
            all_creators = bool(re.search(r"记账人|成员|谁|每个人|各人|所有人|大家", question))
            self_creator = bool(
                re.search(
                    r"(?:我|本人|自己)(?:的)?(?:记录|记的|记账|录入|添加)|"
                    r"(?:记账人|记录人).{0,4}(?:我|本人|自己)",
                    question,
                )
            ) or (all_creators and bool(re.search(r"我|本人|自己", question)))
            return {
                "currentUserId": str(self.ledger.user_id) if self_creator else None,
                "bookKind": book.kind,
                "creatorDirectory": [
                    {"userId": str(member.user_id), "name": member.name, "status": member.status}
                    for member in members[:50]
                    if all_creators
                    or (member.name not in {"我", "本人", "自己"} and member.name in question)
                ],
                "creatorDirectoryTruncated": len(members) > 50,
            }

    async def summary(self, conversation_id):
        async with self._access(conversation_id) as conversation:
            return _summary(conversation)

    async def list(self, book_id):
        try:
            await self.ledger.require_book(book_id)
            rows = await self.ledger.session.scalars(
                select(ChatConversation)
                .join(Book)
                .where(
                    ChatConversation.owner_id == self.ledger.user_id,
                    ChatConversation.book_id == book_id,
                    self.ledger.access_condition(),
                    ChatConversation.version > 0,
                    func.cast(ChatConversation.context["schemaVersion"].as_string(), String) == "2",
                )
                .order_by(ChatConversation.updated_at.desc(), ChatConversation.id.desc())
                .limit(20)
            )
            result = [_summary(row) for row in rows]
            await self.ledger.require_book(book_id)
            return result
        finally:
            await self.ledger.session.rollback()

    async def read(self, conversation_id, before=None, *, legacy=False):
        async with self._access(conversation_id, allow_legacy=legacy) as conversation:
            is_v2 = (conversation.context or {}).get("schemaVersion") == 2
            if legacy and is_v2:
                raise ServiceError(409, "此会话已是 V2，请使用新会话接口")
            query = select(ChatTurn).where(ChatTurn.conversation_id == conversation_id)
            if before is not None:
                query = query.where(ChatTurn.sequence < before)
            rows = list(
                await self.ledger.session.scalars(
                    query.order_by(ChatTurn.sequence.desc()).limit(21)
                )
            )
            model = LegacyConversationResponseV2 if legacy else ConversationResponseV2
            result = model(
                **_summary(conversation).model_dump(),
                context=self._context(conversation.context or {}, conversation.month),
                turns=[
                    self._turn(row, conversation.month, legacy=legacy)
                    for row in reversed(rows[:20])
                ],
                has_more=len(rows) > 20,
            )
            await self.ledger.require_book(conversation.book_id)
            return result

    async def existing(self, conversation_id, body):
        async with self._access(conversation_id) as conversation:
            row = await self.ledger.session.get(ChatTurn, body.id)
            if row is None:
                return None
            if row.conversation_id != conversation_id:
                raise ServiceError(404, "消息不存在或无权访问")
            suggestion_id = (row.result or {}).get("request", {}).get("suggestionId")
            if row.text != body.text or suggestion_id != (
                str(body.suggestion_id) if body.suggestion_id else None
            ):
                raise ServiceError(409, "此消息已处理，请读取原回答或使用新编号")
            await self.ledger.require_book(conversation.book_id)
            return self._turn(row, conversation.month)

    async def fork_legacy(self, source_id, body: CreateConversationV2):
        source = await self.load(source_id, allow_legacy=True)
        if source.protocol != 1 or body.book_id != source.book_id:
            raise ServiceError(409, "请为当前账本的旧会话创建新的 V2 会话")
        context = source.context.model_copy(
            update={"result_refs": [], "summary": "", "summary_through_sequence": 0}
        )
        return await self.create(body, context=context)

    async def revise(self, conversation_id, patch: ContextPatch):
        state = await self.load(conversation_id)
        restored = {}
        async with self._access(conversation_id) as conversation:
            for edit in patch.edits:
                if edit.op == "restore" and edit.source_turn_id not in restored:
                    row = await self.ledger.session.scalar(
                        select(ChatTurn).where(
                            ChatTurn.id == edit.source_turn_id,
                            ChatTurn.conversation_id == conversation_id,
                        )
                    )
                    if row is None:
                        raise ServiceError(404, "来源轮次不存在或无权访问")
                    if self._turn(row, conversation.month).completeness != "complete":
                        raise ServiceError(409, "部分结果不能恢复为成功上下文，请重新查询")
                    restored[row.id] = self._context(row.context, conversation.month)
            defaults = initial_context(
                state.initial_month, today=self.today, max_days=self.settings.chat_v2_max_range_days
            )
            context, notices = apply_context_patch(
                state.context,
                patch,
                defaults,
                today=self.today,
                max_days=self.settings.chat_v2_max_range_days,
                restored=restored,
            )
            await self.filters.prepare(state.book_id, context.filters)
            return ContextDraft(conversation_id, state.version, context, notices)

    async def _live_entries(self, book_id, ids):
        if not ids:
            return {}
        rows = (
            (
                await self.ledger.session.execute(
                    select(
                        LedgerEntry.id,
                        LedgerEntry.note,
                        LedgerEntry.date,
                        LedgerEntry.type,
                        LedgerEntry.category_id,
                    )
                    .join(Book, Book.id == LedgerEntry.book_id)
                    .where(
                        Book.id == book_id,
                        self.ledger.access_condition(),
                        LedgerEntry.id.in_(ids),
                        LedgerEntry.is_deleted.is_(False),
                        LedgerEntry.date <= self.today,
                    )
                )
            )
            .mappings()
            .all()
        )
        return {row["id"]: row for row in rows}

    async def save_success(
        self, draft: ContextDraft, body: SendMessageV2, preview: AnswerPreviewV2
    ):
        body = SendMessageV2.model_validate(body.model_dump())
        preview = AnswerPreviewV2.model_validate(preview.model_dump())
        if preview.completeness != "complete":
            raise ServiceError(409, "未完成的回答不能更新成功上下文")
        return await self._save(draft, body, preview)

    async def save_turn(self, draft, body, preview):
        """保存明确的部分消息，但只让完整成功结果更新权威上下文。"""
        body = SendMessageV2.model_validate(body.model_dump())
        preview = AnswerPreviewV2.model_validate(preview.model_dump())
        if preview.completeness == "complete":
            return await self.save_success(draft, body, preview)
        return await self._save(
            draft, body, preview.model_copy(update={"suggestions": []}), partial=True
        )

    async def _save(self, draft, body, preview, *, partial=False):
        try:
            async with self._access(draft.conversation_id) as conversation:
                existing = await self.ledger.session.get(ChatTurn, body.id)
                if existing:
                    stored_suggestion = (
                        (existing.result or {}).get("request", {}).get("suggestionId")
                    )
                    expected_suggestion = str(body.suggestion_id) if body.suggestion_id else None
                    if existing.conversation_id != draft.conversation_id:
                        raise ServiceError(404, "消息不存在或无权访问")
                    if existing.text != body.text or stored_suggestion != expected_suggestion:
                        raise ServiceError(409, "该消息已处理，请读取原回答或使用新编号")
                    return self._turn(existing, conversation.month)
                if conversation.version != draft.expected_version:
                    raise ServiceError(409, "对话已在其他请求更新，请重新读取后继续")
                if conversation.version >= 100:
                    raise ServiceError(409, "此会话已有一百轮问答，请开始新对话")
                previous = self._context(conversation.context, conversation.month)
                candidate = (
                    previous
                    if partial
                    else ChatContextV2.model_validate(draft.context.model_dump())
                )
                if not partial:
                    prepared = await self.filters.prepare(conversation.book_id, candidate.filters)
                    previous_range = candidate.previous_range
                    if previous_range:
                        previous_range = validate_date_range(
                            previous_range,
                            today=self.today,
                            max_days=self.settings.chat_v2_max_range_days,
                        ).date_range
                    candidate = candidate.model_copy(
                        update={"filters": prepared.filters, "previous_range": previous_range}
                    )
                await self._validate_preview(conversation, body.id, preview, candidate)
                sequence = conversation.version + 1
                context = (
                    previous
                    if partial
                    else completed_context(
                        previous,
                        candidate,
                        body.id,
                        sequence,
                        body.text,
                        preview.answer,
                        preview.cards,
                    )
                )
                now = datetime.now(UTC)
                response = TurnResponseV2(
                    **preview.model_dump(),
                    id=body.id,
                    sequence=sequence,
                    text=body.text,
                    context=context,
                    created_at=_timestamp(now),
                )
                # 提前验证本轮输入预算，避免保存无法继续使用的必需筛选。
                model_context(
                    ContextState(
                        conversation.id,
                        self.ledger.user_id,
                        conversation.book_id,
                        conversation.month,
                        sequence,
                        2,
                        context,
                        (response,),
                    ),
                    self.settings,
                )
                result = await self.ledger.session.execute(
                    update(ChatConversation)
                    .where(
                        ChatConversation.id == conversation.id,
                        ChatConversation.owner_id == self.ledger.user_id,
                        ChatConversation.version == draft.expected_version,
                    )
                    .values(
                        context=context.model_dump(mode="json", by_alias=True),
                        version=sequence,
                        title=body.text[:40] if sequence == 1 else conversation.title,
                        updated_at=now,
                    )
                )
                if result.rowcount != 1:
                    raise ServiceError(409, "对话已在其他请求更新，请重新读取后继续")
                self.ledger.session.add(
                    ChatTurn(
                        id=body.id,
                        conversation_id=conversation.id,
                        sequence=sequence,
                        text=body.text,
                        answer=preview.answer,
                        result={
                            "schemaVersion": 2,
                            "payload": preview.model_dump(mode="json", by_alias=True),
                            "request": {
                                "suggestionId": str(body.suggestion_id)
                                if body.suggestion_id
                                else None
                            },
                        },
                        context=context.model_dump(mode="json", by_alias=True),
                        created_at=now,
                    )
                )
                await self.ledger.session.flush()
                await self.ledger.require_book(conversation.book_id)
                await self.ledger.session.commit()
                return response
        except IntegrityError:
            raise ServiceError(409, "消息已由其他请求处理，请重新读取") from None

    async def _suggestion_card(self, conversation, suggestion, turn_id, preview, context):
        reference = suggestion.reference
        if reference is None:
            if suggestion.text in {ENTRY, EXCLUDE, CONTRIBUTION}:
                raise ServiceError(409, "建议缺少必要的结果引用")
            return ResultCard(
                id="scope",
                type="summary",
                title="讨论范围",
                filters=context.filters,
                previous_range=context.previous_range,
                generated_at=0,
            ), {}
        if reference.turn_id == turn_id:
            source = preview
        else:
            row = await self.ledger.session.scalar(
                select(ChatTurn).where(
                    ChatTurn.id == reference.turn_id,
                    ChatTurn.conversation_id == conversation.id,
                )
            )
            if row is None:
                raise ServiceError(404, "建议来源不存在或无权访问")
            source = self._turn(row, conversation.month)
        card = next((card for card in source.cards if card.id == reference.card_id), None)
        if card is None:
            raise ServiceError(404, "建议引用的卡片不存在或无权访问")
        visible = {row.entry_id for row in card.rows if row.entry_id}
        if set(reference.entry_ids) - visible:
            raise ServiceError(404, "建议引用的账单未在来源卡片中展示")
        if suggestion.text in {ENTRY, EXCLUDE}:
            if len(reference.entry_ids) != 1:
                raise ServiceError(409, "建议需要明确的一笔账单")
        elif reference.entry_ids:
            raise ServiceError(409, "该建议不能附带账单操作")
        return card, {fact.id: fact for fact in source.fact_refs}

    async def _validate_preview(
        self, conversation, turn_id, preview, context, *, check_card_entries=True
    ):
        ids = (
            {row.entry_id for card in preview.cards for row in card.rows if row.entry_id}
            if check_card_entries
            else set()
        )
        suggestion_ids = set()
        for suggestion in preview.suggestions:
            if (
                suggestion.source_turn_id != turn_id
                or suggestion.id in suggestion_ids
                or suggestion.text not in TEXTS
            ):
                raise ServiceError(409, "建议来源或内容不正确，请重新生成")
            suggestion_ids.add(suggestion.id)
            card, facts = await self._suggestion_card(
                conversation, suggestion, turn_id, preview, context
            )
            if suggestion.text == CONTRIBUTION and (
                card.type != "comparison" or not category_target(card, facts)
            ):
                raise ServiceError(409, "建议缺少有效的分类差额")
            if suggestion.text == EXCLUDE and context.previous_range is None:
                raise ServiceError(409, "排除后比较需要明确的比较区间")
            if suggestion.text == COMPARE:
                suggestion_plan(
                    suggestion,
                    card,
                    facts,
                    today=self.today,
                    max_days=self.settings.chat_v2_max_range_days,
                    context=context,
                )
            if suggestion.reference:
                ids.update(suggestion.reference.entry_ids)
        if set(await self._live_entries(conversation.book_id, ids)) != ids:
            raise ServiceError(404, "回答或建议中的账单已失效或无权访问，请重新查询")

    async def validate_preview(self, draft, body, preview):
        async with self._access(draft.conversation_id) as conversation:
            if conversation.version != draft.expected_version:
                raise ServiceError(409, "对话已在其他请求更新，请重新读取后继续")
            await self._validate_preview(conversation, body.id, preview, draft.context)
            await self.ledger.require_book(conversation.book_id)

    async def resolve_suggestion(self, conversation_id, body: SendMessageV2):
        body = SendMessageV2.model_validate(body.model_dump())
        if body.suggestion_id is None:
            raise ServiceError(422, "请提供建议编号")
        async with self._access(conversation_id) as conversation:
            rows = await self.ledger.session.scalars(
                select(ChatTurn)
                .where(
                    ChatTurn.conversation_id == conversation_id,
                )
                .order_by(ChatTurn.sequence.desc())
                .limit(100)
            )
            for row in rows:
                turn = self._turn(row, conversation.month)
                suggestion = next(
                    (item for item in turn.suggestions if item.id == body.suggestion_id), None
                )
                if suggestion is None:
                    continue
                if turn.completeness != "complete":
                    raise ServiceError(409, "部分结果的建议不能执行，请重新查询")
                if suggestion.text != body.text or suggestion.source_turn_id != turn.id:
                    raise ServiceError(409, "建议内容或来源不一致，请重新选择")
                await self._validate_preview(
                    conversation,
                    turn.id,
                    turn.model_copy(update={"suggestions": [suggestion]}),
                    turn.context,
                    check_card_entries=False,
                )
                card, facts = await self._suggestion_card(
                    conversation, suggestion, turn.id, turn, turn.context
                )
                plan = suggestion_plan(
                    suggestion,
                    card,
                    facts,
                    today=self.today,
                    max_days=self.settings.chat_v2_max_range_days,
                    context=turn.context,
                )
                await self.ledger.require_book(conversation.book_id)
                return plan
            raise ServiceError(404, "建议不存在或无权访问，请重新选择")

    async def resolve_reference(self, conversation_id, request: ReferenceRequest):
        request = ReferenceRequest.model_validate(request.model_dump())
        async with self._access(conversation_id) as conversation:
            query = select(ChatTurn).where(ChatTurn.conversation_id == conversation_id)
            if request.turn_id:
                query = query.where(ChatTurn.id == request.turn_id)
            rows = list(
                await self.ledger.session.scalars(
                    query.order_by(ChatTurn.sequence.desc()).limit(20)
                )
            )
            if request.turn_id and not rows:
                raise ServiceError(404, "来源轮次不存在或无权访问")
            references = []
            for row in rows:
                turn = self._turn(row, conversation.month)
                if turn.completeness != "complete":
                    if request.turn_id:
                        raise ServiceError(409, "部分结果不能作为后续引用，请重新查询")
                    continue
                cards = [
                    card
                    for card in turn.cards
                    if request.card_id is None or card.id == request.card_id
                ]
                references = [
                    (turn.id, card.id, item.entry_id)
                    for card in cards
                    for item in card.rows
                    if item.entry_id
                ]
                if references:
                    break
            known_ids = {item[2] for item in references}
            if set(request.entry_ids) - known_ids or (request.card_id and not references):
                raise ServiceError(404, "引用不属于当前会话的已展示结果")
            if request.entry_ids:
                references = [item for item in references if item[2] in request.entry_ids]
            references = list(dict.fromkeys(references))
            live = await self._live_entries(conversation.book_id, {item[2] for item in references})
            candidates = []
            seen = set()
            for turn_id, card_id, entry_id in references:
                if entry_id not in live or entry_id in seen:
                    continue
                seen.add(entry_id)
                entry = live[entry_id]
                candidates.append(
                    ReferenceCandidate(
                        reference=ResultReference(
                            turn_id=turn_id, card_id=card_id, entry_ids=[entry_id]
                        ),
                        entry_id=entry_id,
                        label=entry["note"] or CATEGORIES.get(entry["category_id"], ("账单",))[0],
                        date=entry["date"],
                        entry_type=entry["type"],
                    )
                )
            await self.ledger.require_book(conversation.book_id)
            if len(candidates) != len(
                known_ids if not request.entry_ids else set(request.entry_ids)
            ):
                return ReferenceResolution(
                    status="clarify",
                    candidates=candidates[:20],
                    detail="部分引用已失效，请重新选择账单",
                )
            if not candidates:
                return ReferenceResolution(
                    status="clarify", detail="还没有可引用的账单，请先查询或选择一笔"
                )
            if len(candidates) > 20 or (request.kind == "entry" and len(candidates) != 1):
                return ReferenceResolution(
                    status="clarify",
                    candidates=candidates[:20],
                    detail="有多笔候选，请明确选择哪笔账单",
                )
            return ReferenceResolution(
                status="resolved", candidates=candidates, detail="已按当前有效账单重新核对引用"
            )
