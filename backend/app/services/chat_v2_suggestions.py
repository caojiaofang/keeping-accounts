"""服务端追问模板与受控计划；客户端只提交已保存的建议 ID。"""

from uuid import uuid4

from app.core.errors import ServiceError
from app.schemas.chat_v2 import (
    ComparisonSelection,
    ContextPatch,
    ContextPlanV2,
    ReferenceRequest,
    ResultReference,
    Suggestion,
)
from app.schemas.ledger import EXPENSE_CATEGORIES, INCOME_CATEGORIES
from app.services.analysis import CATEGORIES as CATEGORY_LABELS
from app.services.analysis_v2 import resolve_comparison_range

DETAILS = "查看匹配账单明细"
CATEGORIES = "按分类分开看看"
COMPARE = "和前一段等长时间比呢？"
CONTRIBUTION = "查看差额最大的分类明细"
EXCLUDE = "排除这笔再比较"
ENTRY = "查看这笔账单"
WIDEN = "清空备注关键词再查"
TEXTS = {DETAILS, CATEGORIES, COMPARE, CONTRIBUTION, EXCLUDE, ENTRY, WIDEN}


def category_target(card, facts):
    allowed = EXPENSE_CATEGORIES | INCOME_CATEGORIES
    choices = []
    for row in card.rows:
        if (
            row.is_other
            or row.group_key not in allowed
            or row.label != CATEGORY_LABELS[row.group_key][0]
        ):
            continue
        difference = next(
            (facts[fid] for fid in row.fact_ids if facts[fid].metric == "difference"), None
        )
        if difference is not None:
            choices.append((abs(difference.value), row.group_key))
    return max(choices, default=(0, None))[1]


def make_suggestions(
    turn_id,
    cards,
    facts,
    *,
    today,
    max_days,
    empty=False,
    attach_reference=True,
    allow_exclude=True,
):
    if not cards:
        return []
    primary = cards[0]
    candidates = []

    def add(text, card=primary, entry_id=None):
        candidates.append(
            Suggestion(
                id=uuid4(),
                text=text,
                source_turn_id=turn_id,
                reference=ResultReference(
                    turn_id=turn_id, card_id=card.id, entry_ids=[entry_id] if entry_id else []
                )
                if attach_reference
                else None,
            )
        )

    if empty:
        if primary.filters.keywords.include or primary.filters.keywords.exclude:
            add(WIDEN)
        add(CATEGORIES)
    elif primary.type == "comparison":
        if category_target(primary, facts):
            add(CONTRIBUTION)
        add(DETAILS)
    elif primary.type == "entries":
        entry = next((row.entry_id for row in primary.rows if row.entry_id), None)
        if entry and len(primary.rows) == 1:
            add(ENTRY, entry_id=entry)
        add(CATEGORIES)
    else:
        add(DETAILS)
        add(CATEGORIES)
    try:
        resolve_comparison_range(
            primary.filters.date_range,
            ComparisonSelection(kind="previous_period"),
            today=today,
            max_days=max_days,
        )
    except ServiceError:
        pass
    else:
        add(COMPARE)
    if not empty:
        entry_card = next(
            (card for card in cards if card.type == "entries" and len(card.rows) == 1), None
        )
        if entry_card and primary.type == "comparison" and allow_exclude:
            add(EXCLUDE, card=entry_card, entry_id=entry_card.rows[0].entry_id)
            candidates.insert(0, candidates.pop())
    return list({item.text: item for item in candidates}.values())[:3]


def suggestion_plan(suggestion, card, facts, *, today, max_days, context=None):
    """只依据已保存卡片的范围生成计划；历史金额不进入新轮事实。"""
    if suggestion.text not in TEXTS:
        raise ServiceError(409, "这条建议暂不支持，请直接输入问题")
    scope = context if suggestion.text == EXCLUDE else card
    filters = scope.filters.model_dump(mode="json", by_alias=True)
    edits = [
        {"field": field, "op": "replace", "value": value}
        for field, value in filters.items()
        if value is not None and field != "keywords"
    ]
    for field, value in filters["keywords"].items():
        edits.append({"field": f"keywords.{field}", "op": "replace", "value": value})
    # 默认上下文不带金额界限时也必须清除当前轮可能新增的界限。
    for field in ("amountMin", "amountMax"):
        if filters[field] is None:
            edits.append({"field": field, "op": "clear"})
    previous = scope.previous_range
    if suggestion.text == COMPARE:
        previous = resolve_comparison_range(
            card.filters.date_range,
            ComparisonSelection(kind="previous_period"),
            today=today,
            max_days=max_days,
        ).date_range
    if suggestion.text == CONTRIBUTION:
        category = category_target(card, facts)
        if not category:
            raise ServiceError(409, "分类引用不完整，请重新查询")
        edits = [edit for edit in edits if edit["field"] != "categoryIds"]
        edits.append({"field": "categoryIds", "op": "replace", "value": [category]})
    if suggestion.text == WIDEN:
        edits = [
            edit for edit in edits if edit["field"] not in {"keywords.include", "keywords.exclude"}
        ]
        edits += [
            {"field": "keywords.include", "op": "clear"},
            {"field": "keywords.exclude", "op": "clear"},
        ]
    edits.append(
        {"field": "previousRange", "op": "replace", "value": previous}
        if previous
        else {"field": "previousRange", "op": "clear"}
    )
    reference = None
    if suggestion.text in {ENTRY, EXCLUDE}:
        if suggestion.reference is None or len(suggestion.reference.entry_ids) != 1:
            raise ServiceError(409, "建议需要明确的一笔账单，请重新选择")
        reference = ReferenceRequest(
            turn_id=suggestion.reference.turn_id,
            card_id=suggestion.reference.card_id,
            entry_ids=suggestion.reference.entry_ids,
        )
    return ContextPlanV2(
        patch=ContextPatch.model_validate({"reset": True, "edits": edits}),
        reference=reference,
        reference_action=("exclude" if suggestion.text == EXCLUDE else "inspect")
        if reference
        else None,
    )
