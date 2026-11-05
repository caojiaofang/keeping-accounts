"""从本轮后端事实生成回答；模型自由正文只作重点提示，不作为消费结论。"""

import asyncio
import re
import unicodedata
from dataclasses import dataclass, replace
from decimal import Decimal

from pydantic import ValidationError

from app.core.errors import ServiceError
from app.schemas.chat_v2 import AnswerPreviewV2, SendMessageV2, ToolResult, validate_fact_references
from app.services.analysis import CATEGORIES, yuan
from app.services.chat_v2_context import ContextDraft
from app.services.chat_v2_loop import ChatLoopV2, LoopOutcomeV2, fingerprint
from app.services.chat_v2_suggestions import make_suggestions

REFERENCE = re.compile(r"\{\{([A-Za-z][A-Za-z0-9_.-]{0,95})\}\}")
CHINESE_NUMBER = re.compile(
    r"百分之[零〇一二三四五六七八九十百千万亿两点半]+|"
    r"[零〇一二三四五六七八九十百千万亿两点半]+(?:元|块|笔|次|成|倍|天|日|年|月|个百分点)"
)


def same_filters(left, right):
    return fingerprint("filters", left.model_dump(mode="json")) == fingerprint(
        "filters", right.model_dump(mode="json")
    )


def format_fact(fact):
    if fact.unit == "cents":
        return yuan(fact.value)
    if fact.unit == "count":
        return f"{fact.value} 笔"
    if fact.unit == "basis_points":
        return f"{Decimal(fact.value) / 100:.2f}%"
    if fact.unit == "date":
        return fact.value
    # 自由文本不能成为把未验证数值塞进正文的旁路。
    if any(
        unicodedata.category(char).startswith("N") for char in fact.value
    ) or CHINESE_NUMBER.search(fact.value):
        raise ServiceError(502, "文字事实含未经验证的数值")
    return fact.value


def render_fact_template(text, facts):
    """用于草稿验证；正式事实句由服务器生成，不能据此证明自由文本的语义。"""
    remaining = unicodedata.normalize("NFKC", REFERENCE.sub("", text))
    if (
        any(unicodedata.category(char).startswith("N") for char in remaining)
        or re.search(r"[¥￥%％{}]", remaining)
        or CHINESE_NUMBER.search(remaining)
    ):
        raise ServiceError(502, "回答草稿含未经验证的数值")
    ids = REFERENCE.findall(text)
    if any(fid not in facts for fid in ids):
        raise ServiceError(502, "回答草稿引用了本轮不存在的事实")
    rendered = REFERENCE.sub(lambda match: format_fact(facts[match[1]]), text)
    if len(rendered) > 2500:
        raise ServiceError(502, "回答草稿过长")
    return rendered


def collect_results(results):
    cards, facts, calls, snapshots = [], {}, set(), {}
    for raw in results:
        result = ToolResult.model_validate(raw.model_dump())
        if result.status != "complete" or result.call_id in calls:
            raise ServiceError(502, "回答事实来源不完整")
        calls.add(result.call_id)
        for fact in result.fact_refs:
            if fact.id in facts or not fact.id.startswith(result.call_id + "."):
                raise ServiceError(502, "回答事实来源冲突")
            unit = {
                "total": "cents",
                "previous_total": "cents",
                "difference": "cents",
                "amount": "cents",
                "count": "count",
                "previous_count": "count",
                "change_rate": "basis_points",
                "share": "basis_points",
            }.get(fact.metric)
            if unit is not None and fact.unit != unit:
                raise ServiceError(502, "回答事实的指标与单位不一致")
            facts[fact.id] = fact
        cards.extend(result.cards)
        for card in result.cards:
            # 相同筛选和收支的重复聚合如已变化，不能拼成一次严格归因。
            for kind in ("expense", "income"):
                totals = tuple(
                    (facts[fid].metric, facts[fid].value)
                    for fid in card.fact_ids
                    if f".{kind}." in fid and facts[fid].metric in {"total", "count"}
                )
                if not totals:
                    continue
                key = (card.filters.model_dump_json(), kind)
                if key in snapshots and snapshots[key] != totals:
                    raise ServiceError(409, "账单在分析期间已变化，请重新查询")
                snapshots[key] = totals
    validate_fact_references(cards, list(facts.values()))
    return cards, facts


def select_cards(cards, template, question):
    if not cards:
        return []
    referenced = set(REFERENCE.findall(template))
    comparisons = [card for card in cards if card.type == "comparison"]
    entries = [card for card in cards if card.type == "entries"]
    if comparisons and re.search(r"为什么|多花|少花|差额|相比|比呢|比较", question):
        primary = comparisons[-1]
    elif entries and re.search(r"明细|哪笔|这笔|上次|查找|搜索|耳机", question):
        primary = entries[-1]
    else:
        primary = next(
            (
                card
                for card in reversed(cards)
                if referenced.intersection(
                    card.fact_ids + [fid for row in card.rows for fid in row.fact_ids]
                )
            ),
            cards[-1],
        )
    call = (
        next(iter(primary.fact_ids)).split(".")[0] if primary.fact_ids else primary.id.split(".")[0]
    )
    selected = [primary] + [
        card for card in cards if card != primary and card.id.startswith(call + ".")
    ]
    if primary.type == "comparison" and entries:
        selected.append(entries[-1])
    return list({card.id: card for card in selected}.values())[:3]


def card_text(card, facts):
    parts = []
    for kind in ("expense", "income"):
        ids = [fid for fid in card.fact_ids if f".{kind}." in fid]
        if ids:
            parts.append(_type_text(card, facts, ids))
    if not parts:
        raise ServiceError(502, "回答缺少明确的收支事实")
    return "\n".join(parts)


def _type_text(card, facts, ids):
    values = {facts[fid].metric: facts[fid] for fid in ids}
    total, count = values.get("total"), values.get("count")
    if total is None or count is None:
        raise ServiceError(502, "回答缺少完整的合计事实")
    kind = "收入" if ".income." in total.id else "支出"
    scope = f"{card.filters.date_range.start} 至 {card.filters.date_range.end}"
    if count.value == 0:
        text = f"{scope}，当前筛选下没有{kind}记录。没有记录不代表实际没有收支。"
    else:
        text = f"{scope}，当前筛选记录的{kind}合计 {format_fact(total)}，共 {format_fact(count)}。"
    if card.type == "comparison":
        previous, previous_count, difference = (
            values.get(name) for name in ("previous_total", "previous_count", "difference")
        )
        if previous is None or previous_count is None or difference is None:
            raise ServiceError(502, "比较回答缺少两期完整事实")
        text += (
            f"上期为 {card.previous_range.start} 至 {card.previous_range.end}，"
            f"记录{kind} {format_fact(previous)}，差额为 {format_fact(difference)}。"
        )
        if not previous_count.value:
            text += "上期没有匹配记录，不能据此推断真实消费增长。"
        elif previous.value == 0:
            text += "上期金额为零，不计算增长率。"
        elif values.get("change_rate"):
            text += f"相对上期变化率为 {format_fact(values['change_rate'])}。"
        contributions = []
        for row in card.rows:
            if (
                row.is_other
                or row.group_key not in CATEGORIES
                or row.label != CATEGORIES[row.group_key][0]
            ):
                continue
            fact = next(
                (facts[fid] for fid in row.fact_ids if facts[fid].metric == "difference"), None
            )
            if fact and fact.value:
                contributions.append(f"{CATEGORIES[row.group_key][0]} {format_fact(fact)}")
        if contributions:
            text += "分类差额：" + "；".join(contributions[:3]) + "。"
        text += "差额仅说明账单数值贡献，不能证明实际消费原因。"
    if card.type == "entries" and count.value:
        text += "下方为本页匹配账单，合计与笔数覆盖全部匹配记录。"
    return text


@dataclass(frozen=True)
class PreparedAnswerV2:
    draft: ContextDraft
    preview: AnswerPreviewV2
    model_requests: int
    tool_calls: int
    deadline: float


class AnswerServiceV2:
    def __init__(self, loop: ChatLoopV2):
        self.loop, self.store = loop, loop.store

    async def run(self, conversation_id, body):
        return await self.prepare(await self.loop.run(conversation_id, body), body)

    async def prepare(self, outcome: LoopOutcomeV2, body):
        body = SendMessageV2.model_validate(body.model_dump())
        if (
            outcome.message_id != body.id
            or outcome.question != body.text
            or outcome.suggestion_id != body.suggestion_id
        ):
            raise ServiceError(409, "回答与本轮消息不一致，请重新查询")
        if outcome.deadline is None:
            raise ServiceError(422, "回答缺少本轮期限，请重新查询")
        try:
            async with asyncio.timeout_at(outcome.deadline):
                if asyncio.get_running_loop().time() >= outcome.deadline:
                    raise TimeoutError
                return await self._prepare(outcome, body)
        except TimeoutError:
            raise ServiceError(504, "本轮分析超时，请缩小范围后重试") from None

    async def _prepare(self, outcome, body):
        state = await self.loop._current(
            outcome.draft.conversation_id, outcome.draft.expected_version
        )
        try:
            cards, facts = collect_results(outcome.tool_results)
        except (ValidationError, ValueError):
            raise ServiceError(502, "回答事实不完整，请重新查询") from None
        missing = list(outcome.missing)
        if outcome.action != "clarify":
            try:
                render_fact_template(outcome.answer_template, facts)
            except ServiceError as error:
                missing.append(error.detail + "，已使用后端事实说明")
        selected = select_cards(cards, outcome.answer_template, body.text)
        primary_call = selected[0].id.split(".")[0] if selected else None
        primary_tool = next(
            (result.name for result in outcome.tool_results if result.call_id == primary_call), None
        )
        if (
            selected
            and primary_tool != "get_entry"
            and outcome.action == "answer"
            and not same_filters(selected[0].filters, outcome.draft.context.filters)
        ):
            raise ServiceError(409, "本轮主统计与讨论筛选不一致，请明确条件后重新查询")
        if outcome.action == "answer" and not selected:
            raise ServiceError(502, "没有可验证的账单事实，请重新查询")
        draft = outcome.draft
        if (
            selected
            and selected[0].type == "comparison"
            and same_filters(selected[0].filters, draft.context.filters)
        ):
            if (
                draft.context.previous_range
                and draft.context.previous_range != selected[0].previous_range
            ):
                raise ServiceError(409, "比较范围与本轮条件不一致，请重新查询")
            draft = replace(
                draft,
                context=draft.context.model_copy(
                    update={
                        "previous_range": selected[0].previous_range,
                    }
                ),
            )
        paragraphs = [card_text(card, facts) for card in selected[:2]]
        if outcome.action == "clarify":
            paragraphs = [
                outcome.reference.detail
                if outcome.reference and outcome.reference.status == "clarify"
                else "请明确要查询的日期范围、收支类型或具体账单。"
            ]
        if outcome.action == "partial" or missing:
            paragraphs.insert(0, "本轮分析未全部完成，以下仅为已验证的查询结果。")
        notices = list(
            dict.fromkeys(
                [
                    *draft.notices,
                    *(note for result in outcome.tool_results for note in result.notices),
                ]
            )
        )
        paragraphs.extend(notices)
        answer = "\n".join(paragraphs)
        if len(answer) > 2500:
            raise ServiceError(502, "回答内容过长，请缩小查询范围")
        # 简单汇总只给直接回答；有分组、明细或比较时才附卡片。
        hide = bool(selected) and all(
            card.type == "summary"
            and not card.rows
            and same_filters(card.filters, draft.context.filters)
            for card in selected
        )
        shown = [] if hide or outcome.action == "clarify" else selected
        empty = bool(selected) and all(
            facts[fid].value == 0
            for card in selected
            for fid in card.fact_ids
            if facts[fid].metric == "count"
        )
        suggestions = (
            []
            if outcome.action == "clarify"
            else make_suggestions(
                body.id,
                selected,
                facts,
                today=self.store.today,
                max_days=self.store.settings.chat_v2_max_range_days,
                empty=empty,
                attach_reference=not hide,
                allow_exclude=same_filters(selected[0].filters, draft.context.filters)
                and draft.context.previous_range is not None,
            )
        )
        # 只携带本次所用工具的事实，不能把之前对话中的同名 q1 当成本轮事实。
        used_calls = {fid.split(".")[0] for card in selected for fid in card.fact_ids}
        preview = AnswerPreviewV2(
            answer=answer,
            completeness="partial" if outcome.action == "partial" or missing else "complete",
            missing=list(dict.fromkeys(missing))
            or (["查询未全部完成"] if outcome.action == "partial" else []),
            cards=shown,
            fact_refs=[fact for fact in facts.values() if fact.source_call_id in used_calls],
            suggestions=suggestions,
        )
        await self.store.validate_preview(draft, body, preview)
        await self.loop._current(state.conversation_id, state.version)
        return PreparedAnswerV2(
            draft, preview, outcome.model_requests, outcome.tool_calls, outcome.deadline
        )
