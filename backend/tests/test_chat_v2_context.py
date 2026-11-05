"""上下文编辑、历史摘要和输入预算；不调用模型或远程存储。"""

import json
from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import ServiceError
from app.schemas.chat_v2 import ContextPatch, TurnResponseV2
from app.services.chat_v2_context import (
    ContextState,
    apply_context_patch,
    completed_context,
    convert_legacy_context,
    initial_context,
    model_context,
)

TODAY = date(2026, 10, 27)


def edit(current, edits, **kwargs):
    return apply_context_patch(
        current,
        ContextPatch.model_validate({"edits": edits, **kwargs}),
        initial_context("2026-10", today=TODAY),
        today=TODAY,
    )


def test_add_replace_clear_are_distinct_and_do_not_mutate_source():
    base = initial_context("2026-10", today=TODAY)
    first, _ = edit(base, [{"field": "keywords.include", "op": "replace", "value": ["奶茶"]}])
    expanded, _ = edit(
        first, [{"field": "keywords.include", "op": "add", "value": ["咖啡", "奶茶"]}]
    )
    assert expanded.filters.keywords.include == ["奶茶", "咖啡"]
    replaced, _ = edit(
        expanded, [{"field": "keywords.include", "op": "replace", "value": ["咖啡"]}]
    )
    assert replaced.filters.keywords.include == ["咖啡"]
    cleared, _ = edit(replaced, [{"field": "keywords.include", "op": "clear"}])
    assert cleared.filters.keywords.include == []
    assert first.filters.keywords.include == ["奶茶"] and not base.filters.keywords.include


def test_income_switch_clears_only_inherited_incompatible_category():
    current, _ = edit(
        initial_context("2026-10", today=TODAY),
        [{"field": "categoryIds", "op": "replace", "value": ["coffee"]}],
    )
    changed, notices = edit(current, [{"field": "entryType", "op": "replace", "value": "income"}])
    assert changed.filters.entry_type == "income" and changed.filters.category_ids == [] and notices
    with pytest.raises(ServiceError):
        edit(
            current,
            [
                {"field": "entryType", "op": "replace", "value": "income"},
                {"field": "categoryIds", "op": "replace", "value": ["coffee"]},
            ],
        )


def test_date_change_clears_old_comparison_and_explicit_comparison_wins():
    current, _ = edit(
        initial_context("2026-10", today=TODAY),
        [
            {
                "field": "previousRange",
                "op": "replace",
                "value": {"start": "2026-09-01", "end": "2026-09-27"},
            }
        ],
    )
    date_edit = {
        "field": "dateRange",
        "op": "replace",
        "value": {"start": "2026-10-21", "end": "2026-10-27"},
    }
    changed, notices = edit(current, [date_edit])
    assert changed.previous_range is None and notices
    changed, _ = edit(
        current,
        [
            date_edit,
            {
                "field": "previousRange",
                "op": "replace",
                "value": {"start": "2026-10-14", "end": "2026-10-20"},
            },
        ],
    )
    assert changed.previous_range.start == date(2026, 10, 14)


def test_restore_and_field_provenance():
    defaults = initial_context("2026-10", today=TODAY)
    original, _ = edit(defaults, [{"field": "categoryIds", "op": "replace", "value": ["food"]}])
    current, _ = edit(original, [{"field": "categoryIds", "op": "replace", "value": ["coffee"]}])
    source_id = uuid4()
    patch = ContextPatch.model_validate(
        {"edits": [{"field": "categoryIds", "op": "restore", "sourceTurnId": str(source_id)}]}
    )
    restored, _ = apply_context_patch(
        current, patch, defaults, today=TODAY, restored={source_id: original}
    )
    assert restored.filters.category_ids == ["food"]
    assert (
        next(item for item in restored.sources if item.field == "categoryIds").restored_from
        == source_id
    )
    with pytest.raises(ServiceError):
        apply_context_patch(current, patch, defaults, today=TODAY)


def test_reset_and_amount_boundary_clear():
    current, _ = edit(
        initial_context("2026-10", today=TODAY),
        [
            {"field": "amountMin", "op": "replace", "value": 20000},
            {"field": "minInclusive", "op": "replace", "value": False},
            {"field": "accounts", "op": "add", "value": ["微信"]},
        ],
    )
    cleared, _ = edit(current, [{"field": "amountMin", "op": "clear"}])
    assert cleared.filters.amount_min is None and cleared.filters.min_inclusive
    reset, _ = edit(current, [], reset=True)
    assert reset.filters == initial_context("2026-10", today=TODAY).filters


@pytest.mark.parametrize(
    "change",
    [
        {"field": "userId", "op": "replace", "value": "身份"},
        {"field": "amountMin", "op": "add", "value": [100]},
        {"field": "categoryIds", "op": "clear", "value": ["food"]},
        {"field": "categoryIds", "op": "restore"},
        {"field": "accounts", "op": "replace"},
    ],
)
def test_invalid_edit_shapes_are_rejected(change):
    with pytest.raises(ValidationError):
        ContextPatch.model_validate({"edits": [change]})


def test_legacy_conversion_does_not_invent_references_or_answers():
    converted = convert_legacy_context(
        {"month": "2026-09", "categoryId": "food", "entryType": "expense"},
        initial_month="2026-10",
        today=TODAY,
    )
    assert converted.filters.date_range.end == date(2026, 9, 30)
    assert converted.filters.category_ids == ["food"] and converted.result_refs == []
    assert not converted.summary


def make_turn(sequence, *, answer="已完成查询。", context=None):
    return TurnResponseV2(
        id=uuid4(),
        sequence=sequence,
        text="查看本月",
        answer=answer,
        completeness="complete",
        context=context or initial_context("2026-10", today=TODAY),
        created_at=0,
    )


def test_summary_preserves_whole_notes_and_raw_answers():
    context = initial_context("2026-10", today=TODAY)
    raw = "金额 ¥123.45。" * 210
    for sequence in range(1, 25):
        context = completed_context(context, context, uuid4(), sequence, "查看本月", raw, [])
    assert len(context.summary) <= 2000 and context.summary_through_sequence == 24
    for line in context.summary.splitlines():
        json.loads(line)
    assert "123.4" not in context.summary  # 超长回答省略整段，不能留下半个金额。
    assert raw.endswith("¥123.45。")


def test_model_input_budget_drops_whole_old_turns_but_keeps_filters_and_last_answer():
    context = initial_context("2026-10", today=TODAY)
    turns = tuple(
        make_turn(index, answer="完整回答。" * 180, context=context) for index in range(1, 7)
    )
    state = ContextState(uuid4(), uuid4(), uuid4(), "2026-10", 6, 2, context, turns)
    payload = model_context(state, Settings(_env_file=None, chat_v2_context_max_chars=4000))
    assert len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))) <= 4000
    assert payload["recentTurns"][-1]["answer"] == turns[-1].answer
    assert payload["context"]["filters"] == context.filters.model_dump(mode="json", by_alias=True)
    assert (
        payload["omittedHistory"] and payload["historyIsSnapshot"] and payload["summaryIsRolling"]
    )


def test_budget_never_silently_drops_required_filters():
    context = initial_context("2026-10", today=TODAY).model_copy(
        update={
            "filters": initial_context("2026-10", today=TODAY).filters.model_copy(
                update={"excluded_entry_ids": [uuid4() for _ in range(100)]}
            )
        }
    )
    state = ContextState(
        uuid4(),
        uuid4(),
        uuid4(),
        "2026-10",
        1,
        2,
        context,
        (make_turn(1, answer="回答" * 1000, context=context),),
    )
    with pytest.raises(ServiceError, match="过长"):
        model_context(state, Settings(_env_file=None, chat_v2_context_max_chars=4000))
