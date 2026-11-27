"""识别明确标注的试算金额，保持账本筛选与预算试算相互独立。"""

import re
import unicodedata
from dataclasses import dataclass

from app.schemas.chat_v2 import BudgetTrialV2
from app.schemas.ledger import MAX_CENTS
from app.services.analysis import yuan

# 逗号只有在数字之间才可能是千位分隔符，完整金额仍需单独核对。
CLAUSES = re.compile(
    r"[。;!?、\n]|(?<![0-9]),|,(?![0-9])|(?<=[0-9元块])\s*(?:和|并且)?\s*(?=预算|(?:已经|已)花)"
)
MONEY = r"(?:人民币\s*)?(?:¥\s*)?(?P<amount>[+−-]?[0-9.,]{1,32}|[零〇])\s*(?:元|块钱|块)?"
VALID_AMOUNT = re.compile(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]{1,2})?")
BUDGET_ROLE = r"预算(?:金额|上限)?"
SPENT_ROLE = (
    r"(?:实际|累计|总共|一共|目前)?(?:花了|花费了?|花掉了?|消费了?|支出了?|用掉了?|用了"
    r"|(?:已经|已)(?:花|用|支出)了?)(?:金额)?"
)
CHANGE = r"改(?:成|为|到)?|调整到|调整为|上调到|下调到|调到|换成|更正为|修正为|提到|降到|变成"
AMOUNT_INPUT = re.compile(
    rf"(?P<prefix>[\u3400-\u9fffA-Za-z\s]{{0,32}}?)"
    rf"(?P<role>{BUDGET_ROLE}|{SPENT_ROLE})\s*(?P<change>{CHANGE})?\s*(?:是|为|[=:])?\s*{MONEY}\s*(?:呢|吧|呀)?"
)
BARE_INPUT = re.compile(rf"(?:那(?:么)?\s*)?(?P<change>{CHANGE})?\s*{MONEY}\s*(?:呢|吧)?")
FOLLOW_UP = re.compile(r"^(?:(?:那(?:么)?|如果|假设|刚才(?:的)?|前面(?:的)?|其实|那就)\s*)+")
COURTESY = re.compile(
    r"^(?:(?:帮我(?:算(?:一)?下|算算)?|请(?:帮我)?|我(?:的)?|按我说的|试算(?:一下)?|只)\s*)+"
)
TRIAL_ASK = re.compile(
    r"(?:请问\s*)?(?:还剩(?:下)?多少(?:钱|元)?|还能花多少(?:钱|元)?|"
    r"(?:剩下|剩余|余额)(?:有)?多少(?:钱|元)?|"
    r"(?:有没有|是否|会不会|会|有)?超支(?:了)?(?:吗)?|超(?:了|支|出预算)多少(?:钱|元)?|"
    r"超预算(?:了)?吗|帮我(?:算算|算(?:一)?下)|试算(?:一下)?|算算|算一下)(?:呢|呀|吗)?$"
)
ROLE_ASK = re.compile(
    rf"(?:{BUDGET_ROLE}|{SPENT_ROLE})\s*(?:是|为)?\s*(?:(?:有)?多少(?:钱|元)?|未知|不知道|不清楚|待补)?$"
)
EXTERNAL_AMOUNTS = re.compile(
    r"(?:按|根据|查|从|读取).{0,8}(?:账单|账本)|(?:账单|账本)(?:里|上|内)"
)
BUDGET_SETTING = re.compile(r"(?:保存|创建|设置|设定|设(?:一)?个|更新).{0,8}预算")
AMBIGUOUS = re.compile(
    r"剩余预算|还剩预算|预算余额|不算|不含|不计|排除|没花|没有花|未花|未支出|没支出|尚未|还没|不是|"
    r"分别|或者|大概|大约|差不多|左右|至少|不到|预计|计划花|打算花"
)
SPENT_MENTION = re.compile(SPENT_ROLE)
MISSING_FORMAT = f"请用数字填写人民币金额，范围是 {yuan(0)} 到 {yuan(MAX_CENTS)}，最多两位小数。"


@dataclass(frozen=True)
class TrialReply:
    state: BudgetTrialV2
    text: str


def _cents(raw):
    if raw in {"零", "〇"}:
        return 0
    if not VALID_AMOUNT.fullmatch(raw):
        return None
    whole, _, decimals = raw.replace(",", "").partition(".")
    cents = int(whole) * 100 + int(decimals.ljust(2, "0"))
    return cents if cents <= MAX_CENTS else None


def _trial_reply(values):
    budget, spent = values.budget, values.spent
    if budget is None and spent is None:
        text = "请告诉我预算和已经花了多少，我可以帮你试算还剩多少。"
    elif budget is None:
        text = f"按你提供的金额，已花 {yuan(spent)}。预算是多少？"
    elif spent is None:
        text = f"按你提供的金额，预算 {yuan(budget)}。已经花了多少？"
    else:
        remaining = budget - spent
        if remaining > 0:
            result = f"还剩 {yuan(remaining)}"
        elif remaining < 0:
            result = f"超支 {yuan(-remaining)}"
        else:
            result = f"预算刚好用完，剩余 {yuan(0)}"
        text = f"按你提供的金额，预算 {yuan(budget)}，已花 {yuan(spent)}，{result}。"
    return TrialReply(values, text)


def budget_trial(question: str, previous: BudgetTrialV2 | None) -> TrialReply | None:
    if previous is None and "预算" not in question:
        return None
    text = unicodedata.normalize("NFKC", question.replace("，", "\n")).strip()
    if EXTERNAL_AMOUNTS.search(text) or BUDGET_SETTING.search(text):
        return None
    clauses = [part.strip() for part in CLAUSES.split(text) if part.strip()]
    values, topics = {}, set()
    unparsed = []
    revision = False
    asks_for_trial = False
    repeated_amount = False
    for clause in clauses:
        clause, asks = TRIAL_ASK.subn("", clause)
        asks_for_trial = asks_for_trial or bool(asks)
        clause = clause.strip()
        if not clause or ROLE_ASK.fullmatch(clause):
            continue
        if FOLLOW_UP.fullmatch(clause):
            revision = True
            continue
        match = AMOUNT_INPUT.fullmatch(clause)
        if match is None:
            unparsed.append(clause)
            continue
        field = "budget" if match["role"].startswith("预算") else "spent"
        prefix = COURTESY.sub("", match["prefix"].strip())
        prefix, following = FOLLOW_UP.subn("", prefix)
        revision = revision or bool(match["change"] or following)
        topic = prefix.removesuffix("的").strip()
        if topic:
            topics.add(topic)
        if field in values:
            repeated_amount = True
        values[field] = _cents(match["amount"])

    if not values and previous and len(unparsed) == 1:
        bare = BARE_INPUT.fullmatch(unparsed[0])
        if bare:
            cents = _cents(bare["amount"])
            if cents is None:
                return TrialReply(previous, MISSING_FORMAT)
            for provided, missing in (("budget", "spent"), ("spent", "budget")):
                if getattr(previous, provided) is not None and getattr(previous, missing) is None:
                    if not bare["change"]:
                        return _trial_reply(previous.model_copy(update={missing: cents}))
            return TrialReply(previous, "这个数字是预算还是已花金额？请说明要填哪一项。")

    explicit_estimate = "预算" in text and ("试算" in text or SPENT_MENTION.search(text))
    if not values and not asks_for_trial and not explicit_estimate:
        return None
    if repeated_amount:
        return TrialReply(BudgetTrialV2(), "同一项有多个金额。请确认要用的预算和已花金额。")
    if len(topics) > 1 or AMBIGUOUS.search(text):
        return TrialReply(BudgetTrialV2(), "请给出同一项开支的确切预算和已花金额。")
    topic = next(iter(topics), "")
    same_topic = previous is not None and (not topic or topic == previous.topic)
    filling_missing = (
        same_topic and len(values) == 1 and getattr(previous, next(iter(values))) is None
    )
    base = (
        previous
        if same_topic and (revision or filling_missing or not values)
        else BudgetTrialV2(topic=topic)
    )
    if unparsed:
        if not values and "试算" in text and not SPENT_MENTION.search(text):
            return _trial_reply(BudgetTrialV2())
        return TrialReply(
            base, "请注明预算和已花金额。用数字填写，例如「预算 600 元，已花 480 元」。"
        )
    if any(cents is None for cents in values.values()):
        clean = {field: cents for field, cents in values.items() if cents is not None}
        return TrialReply(base.model_copy(update=clean), MISSING_FORMAT)
    return _trial_reply(base.model_copy(update=values))
