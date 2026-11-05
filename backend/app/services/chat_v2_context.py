"""可修正上下文与有限模型输入；不推断自然语言，不将历史金额当作当前事实。"""

import json
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import ServiceError
from app.schemas.chat_v2 import (
    ChatContextV2,
    ContextPatch,
    DateRange,
    DateSelection,
    FieldSource,
    ResultReference,
    TurnResponseV2,
)
from app.schemas.ledger import EXPENSE_CATEGORIES, INCOME_CATEGORIES
from app.services.analysis_v2 import normalize_filter, resolve_date_range, validate_date_range


@dataclass(frozen=True)
class ContextState:
    conversation_id: UUID
    owner_id: UUID
    book_id: UUID
    initial_month: str
    version: int
    protocol: int
    context: ChatContextV2
    recent_turns: tuple[TurnResponseV2, ...]


@dataclass(frozen=True)
class ContextDraft:
    conversation_id: UUID
    expected_version: int
    context: ChatContextV2
    notices: tuple[str, ...]


def initial_context(month: str, *, today: date, max_days=366):
    resolved = resolve_date_range(
        DateSelection(kind="month", month=month), today=today, max_days=max_days
    )
    return ChatContextV2(
        filters={"dateRange": resolved.date_range},
        sources=[
            FieldSource(field="dateRange", source="default"),
            FieldSource(field="entryType", source="default"),
        ],
    )


def convert_legacy_context(value: dict, *, initial_month: str, today: date, max_days=366):
    context = initial_context(value.get("month", initial_month), today=today, max_days=max_days)
    filters = context.filters.model_dump(mode="json", by_alias=True)
    filters["entryType"] = value.get("entryType", "expense")
    filters["categoryIds"] = [value["categoryId"]] if value.get("categoryId") else []
    normalized, _ = normalize_filter(
        type(context.filters).model_validate(filters), today=today, max_days=max_days
    )
    sources = [
        FieldSource(field="dateRange", source="inherited"),
        FieldSource(field="entryType", source="inherited"),
    ]
    if normalized.category_ids:
        sources.append(FieldSource(field="categoryIds", source="inherited"))
    return context.model_copy(update={"filters": normalized, "sources": sources})


def _get(values, path):
    parts = path.split(".")
    return values[parts[0]][parts[1]] if len(parts) == 2 else values[parts[0]]


def _set(values, path, value):
    parts = path.split(".")
    if len(parts) == 2:
        values[parts[0]][parts[1]] = value
    else:
        values[parts[0]] = value


def apply_context_patch(
    current: ChatContextV2,
    patch: ContextPatch,
    defaults: ChatContextV2,
    *,
    today: date,
    max_days=366,
    restored=None,
):
    patch = ContextPatch.model_validate(patch.model_dump())
    restored = restored or {}
    base = defaults if patch.reset else current
    values = base.filters.model_dump(mode="json", by_alias=True)
    values["previousRange"] = (
        base.previous_range.model_dump(mode="json", by_alias=True) if base.previous_range else None
    )
    default_values = defaults.filters.model_dump(mode="json", by_alias=True)
    default_values["previousRange"] = None
    sources = {
        item.field: item.model_copy(
            update={"source": "default" if item.source == "default" else "inherited"}
        )
        for item in base.sources
    }
    touched = {edit.field for edit in patch.edits}
    notices = []
    for edit in patch.edits:
        if edit.op == "restore":
            if edit.source_turn_id not in restored:
                raise ServiceError(404, "来源轮次不存在或无权访问")
            source = restored[edit.source_turn_id]
            restored_values = source.filters.model_dump(mode="json", by_alias=True)
            restored_values["previousRange"] = (
                source.previous_range.model_dump(mode="json") if source.previous_range else None
            )
            value = _get(restored_values, edit.field)
        elif edit.op == "clear":
            value = _get(default_values, edit.field)
        else:
            value = (
                edit.value.model_dump(mode="json")
                if hasattr(edit.value, "model_dump")
                else edit.value
            )
            if edit.op == "add":
                value = list(_get(values, edit.field)) + value
        _set(values, edit.field, value)
        sources[edit.field] = FieldSource(
            field=edit.field, source="explicit", restored_from=edit.source_turn_id
        )
        if edit.op == "clear" and edit.field in {"amountMin", "amountMax"}:
            boundary = "minInclusive" if edit.field == "amountMin" else "maxInclusive"
            _set(values, boundary, True)
            sources[boundary] = FieldSource(field=boundary, source="default")
    if values["entryType"] != current.filters.entry_type and "categoryIds" not in touched:
        allowed = (
            EXPENSE_CATEGORIES
            if values["entryType"] == "expense"
            else INCOME_CATEGORIES
            if values["entryType"] == "income"
            else EXPENSE_CATEGORIES | INCOME_CATEGORIES
        )
        if set(values["categoryIds"]) - allowed:
            values["categoryIds"] = []
            sources["categoryIds"] = FieldSource(field="categoryIds", source="default")
            notices.append("收支类型已改变，已清除不匹配的分类条件。")
    if (
        values["dateRange"] != current.filters.date_range.model_dump(mode="json")
        and "previousRange" not in touched
    ):
        if values["previousRange"] is not None:
            notices.append("查询日期已改变，已清除原比较区间。")
        values["previousRange"] = None
        sources["previousRange"] = FieldSource(field="previousRange", source="default")
    previous_range = values.pop("previousRange")
    try:
        filters, date_notices = normalize_filter(
            type(current.filters).model_validate(values), today=today, max_days=max_days
        )
        if previous_range:
            previous = validate_date_range(
                DateRange.model_validate(previous_range), today=today, max_days=max_days
            )
            previous_range = previous.date_range
            notices.extend(previous.notices)
        result = ChatContextV2(
            filters=filters,
            previous_range=previous_range,
            sources=list(sources.values()),
            result_refs=current.result_refs,
            summary=current.summary,
            summary_through_sequence=current.summary_through_sequence,
        )
    except ValidationError:
        raise ServiceError(422, "上下文修改与筛选条件不匹配，请重新说明") from None
    return result, tuple(notices) + date_notices


def completed_context(
    previous: ChatContextV2, candidate: ChatContextV2, turn_id, sequence, text, answer, cards
):
    refs = list(previous.result_refs)
    refs.extend(
        ResultReference(
            turn_id=turn_id,
            card_id=card.id,
            entry_ids=list(dict.fromkeys(row.entry_id for row in card.rows if row.entry_id)),
        )
        for card in cards
    )
    # 保留完整笔记边界，不截断半个金额或 UUID；这是历史快照摘要，不是最新账单事实。
    note = json.dumps(
        {"sequence": sequence, "turnId": str(turn_id), "question": text, "answer": answer},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    notes = previous.summary.splitlines() if previous.summary else []
    if len(note) <= 2000:
        notes.append(note)
    else:
        notes.append(
            json.dumps(
                {
                    "sequence": sequence,
                    "turnId": str(turn_id),
                    "question": text,
                    "answer": "回答较长，请读取原始轮次；未截断数值。",
                },
                ensure_ascii=False,
            )
        )
    while len("\n".join(notes)) > 2000:
        notes.pop(0)
    return candidate.model_copy(
        update={
            "result_refs": refs[-20:],
            "summary": "\n".join(notes),
            "summary_through_sequence": sequence,
        }
    )


def model_context(state: ContextState, settings: Settings):
    context = state.context.model_dump(mode="json", by_alias=True)
    recent = [
        {
            "id": str(turn.id),
            "sequence": turn.sequence,
            "question": turn.text,
            "answer": turn.answer,
        }
        for turn in state.recent_turns[-settings.chat_v2_context_recent_turns :]
        if turn.completeness == "complete"
    ]
    payload = {
        "schemaVersion": 2,
        "context": context,
        "recentTurns": recent,
        "historyIsSnapshot": True,
        "omittedHistory": state.version > len(recent),
        "summaryIsRolling": True,
    }

    def length():
        return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    while length() > settings.chat_v2_context_max_chars and len(recent) > 1:
        recent.pop(0)
        payload["omittedHistory"] = True
    while length() > settings.chat_v2_context_max_chars and context["summary"]:
        lines = context["summary"].splitlines()
        context["summary"] = "\n".join(lines[1:])
        payload["omittedHistory"] = True
    while length() > settings.chat_v2_context_max_chars and context["resultRefs"]:
        context["resultRefs"].pop(0)
        payload["omittedHistory"] = True
    if length() > settings.chat_v2_context_max_chars:
        raise ServiceError(422, "当前筛选或回答过长，请清空部分条件或开始新对话")
    return payload
