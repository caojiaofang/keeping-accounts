"""固定业务账单与自然语言表达；金额单位为分，可信今天固定为 2026-10-27。"""

from dataclasses import dataclass
from datetime import date

TODAY = date(2026, 10, 27)
CURRENT = ("2026-10-21", "2026-10-27")
PREVIOUS = ("2026-10-14", "2026-10-20")
# key、记账人、类型、分类、金额、日期、备注、是否删除。
RECORDS = (
    ("tea", "owner", "expense", "coffee", 1500, "2026-10-21", "早餐奶茶", False),
    ("coffee", "owner", "expense", "coffee", 2500, "2026-10-22", "午后咖啡", False),
    ("coffee_food", "owner", "expense", "food", 1500, "2026-10-22", "咖啡和面包", False),
    ("dinner", "member", "expense", "food", 6000, "2026-10-23", "共同晚餐", False),
    ("headphones", "owner", "expense", "shopping", 20000, "2026-10-24", "耳机", False),
    ("tea_note", "owner", "expense", "coffee", 1000, "2026-10-24", "奶茶退款字样的消费备注", False),
    ("member_tea", "member", "expense", "coffee", 3000, "2026-10-25", "周末奶茶", False),
    ("lunch", "owner", "expense", "food", 4500, "2026-10-27", "午餐", False),
    ("refund", "owner", "income", "refund", 5000, "2026-10-26", "退款", False),
    ("previous_tea", "owner", "expense", "coffee", 1000, "2026-10-14", "奶茶", False),
    ("previous_dinner", "member", "expense", "food", 3000, "2026-10-16", "晚餐", False),
    ("previous_train", "owner", "expense", "transport", 2000, "2026-10-20", "地铁", False),
    ("september_food", "owner", "expense", "food", 8000, "2026-09-03", "九月午餐", False),
    ("salary", "owner", "income", "salary", 100000, "2026-09-05", "九月工资", False),
    ("deleted", "owner", "expense", "food", 99999, "2026-10-27", "已删除的奶茶", True),
    ("future", "owner", "expense", "food", 88888, "2026-10-28", "未来记录", False),
)


@dataclass(frozen=True)
class Case:
    name: str
    phrases: tuple[str, str, str]
    total: int | None = None
    count: int | None = None
    scenario: str = "normal"
    prelude: str | None = None
    keywords: tuple[str, ...] | None = None
    previous: tuple[str, str] | None = None
    dates: tuple[str, str] = CURRENT
    tools: tuple[str, ...] = ("summarize_entries",)


CASES = (
    Case(
        "recent_compare",
        (
            "最近七天支出和之前七天相比呢？",
            "对比一下10月21日至27日和10月14日至20日的支出",
            "近七天比前一个七天多花了多少？",
        ),
        40000,
        8,
        previous=PREVIOUS,
        tools=("compare_periods",),
    ),
    Case(
        "keyword_add",
        (
            "咖啡也算上",
            "除了奶茶，再加上备注里有咖啡的支出",
            "奶茶和咖啡只要备注包含其中一个都统计",
        ),
        9500,
        5,
        prelude="tea",
        keywords=("奶茶", "咖啡"),
    ),
    Case(
        "exclude_entry",
        (
            "把这笔耳机排除，再看最近七天支出合计",
            "最近七天的支出不要算这笔耳机",
            "排除刚才展示的耳机账单后，这七天总共花了多少？",
        ),
        20000,
        7,
        prelude="headphones",
        tools=("summarize_entries",),
    ),
    Case(
        "shared_creator",
        (
            "最近七天小林记录的支出有多少？",
            "只统计10月21日至27日记账人是小林的支出",
            "近七天的支出按记账人筛到小林",
        ),
        9000,
        2,
    ),
    Case(
        "empty",
        (
            "最近七天备注包含火星的支出有多少？",
            "查10月21日至27日备注有火星的消费合计",
            "只看近七天备注包含火星的支出，花了多少？",
        ),
        0,
        0,
        keywords=("火星",),
    ),
    Case(
        "zero_base",
        (
            "对比10月21日至27日与10月7日至13日的咖啡茶饮分类支出",
            "咖啡茶饮分类在10月21日至27日比10月7日至13日多多少？",
            "本期10月21日至27日，上期10月7日至13日，比较咖啡茶饮分类的支出",
        ),
        8000,
        4,
        previous=("2026-10-07", "2026-10-13"),
        tools=("compare_periods",),
    ),
    Case(
        "multi_turn",
        (
            "最近七天奶茶支出多少？",
            "统计近七天备注包含奶茶的支出",
            "查一下10月21日至27日备注里有奶茶的消费合计",
        ),
        5500,
        3,
        scenario="multi",
        keywords=("奶茶",),
    ),
    Case(
        "ambiguous",
        ("把这笔排除", "不要算这一笔", "刚说的这笔先不算"),
        scenario="clarify",
        tools=(),
    ),
    Case(
        "permission",
        ("最近七天花了多少？", "查看当前账本的支出", "继续看这个账本的消费明细"),
        scenario="permission",
        tools=(),
    ),
    Case(
        "tool_failure",
        ("最近七天支出合计是多少？", "统计近七天的总支出", "10月21日至27日一共花了多少钱？"),
        scenario="tool_failure",
        tools=(),
    ),
    Case(
        "disconnect_retry",
        ("最近七天的总支出是多少？", "合计一下近七天支出", "10月21日到27日的消费一共多少？"),
        40000,
        8,
        scenario="replay",
    ),
    Case(
        "legacy_restore",
        (
            "继续看刚才范围的支出合计",
            "沿用旧对话的月份和分类，统计支出",
            "还是旧对话里的餐饮分类，支出是多少？",
        ),
        8000,
        1,
        scenario="legacy",
        dates=("2026-09-01", "2026-09-30"),
    ),
    Case(
        "historical_month",
        ("九月份总共花了多少？", "统计2026年9月的支出合计", "看一下上个月的全部支出"),
        8000,
        1,
        dates=("2026-09-01", "2026-09-30"),
    ),
)


def selected_cases(names=()):
    available = {case.name: case for case in CASES}
    unknown = set(names) - available.keys()
    if unknown:
        raise ValueError("存在未知评测场景")
    return [available[name] for name in names] if names else list(CASES)


def matching(records, filters):
    """独立 Python 预期计算，不调用 SQL 查询构造器或工具的聚合实现。"""
    result = []
    for row in records:
        if (
            row["is_deleted"]
            or row.get("foreign")
            or not filters.date_range.start <= row["date"] <= filters.date_range.end
        ):
            continue
        if row["date"] > TODAY or filters.entry_type not in {"both", row["type"]}:
            continue
        if filters.category_ids and row["category_id"] not in filters.category_ids:
            continue
        if filters.creator_ids and row["created_by"] not in filters.creator_ids:
            continue
        if filters.accounts and row["account"] not in filters.accounts:
            continue
        if row["id"] in filters.excluded_entry_ids:
            continue
        if filters.weekdays and row["date"].isoweekday() not in filters.weekdays:
            continue
        if filters.amount_min is not None and (
            row["amount"] < filters.amount_min
            or (row["amount"] == filters.amount_min and not filters.min_inclusive)
        ):
            continue
        if filters.amount_max is not None and (
            row["amount"] > filters.amount_max
            or (row["amount"] == filters.amount_max and not filters.max_inclusive)
        ):
            continue
        words = filters.keywords
        checks = [word.casefold() in row["note"].casefold() for word in words.include]
        if checks and not (all(checks) if words.mode == "all" else any(checks)):
            continue
        if any(word.casefold() in row["note"].casefold() for word in words.exclude):
            continue
        result.append(row)
    return result
