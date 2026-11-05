# 聊聊花费 V2：日期与组合筛选

2026-10-28，第 02 步交付。代码位于 `app/services/analysis_v2.py`，输入结构位于 `app/schemas/chat_v2.py`。

本步提供可执行的日期解析、规范化、权限检查和参数化 SQL 条件，供第 03 步的查账工具复用；未注册 V2 接口、接入模型规划或更改小程序界面。2026-10-28 已随后端 `20261028T031201Z` 发布，验证和回退见[上线记录](releases/20261028T031201Z.md)。

## 1. 日期与可信今天

日期解析必须由后端提供 `today`，业务时区为 `Asia/Shanghai`。现有 `app/services/chat.py` 的 `business_today()` 可提供该日期，不使用客户端日期或模型传入的今天。

`DateRange` 接收 `YYYY-MM-DD` 字符串或后端 `date` 对象，支持 2000-01-01 至 2100-12-31，不接受时间戳、datetime、紧凑日期字符串或带时区时刻。

`resolve_date_range(DateSelection, today=..., max_days=...)` 返回 `ResolvedRange`，包含实际 `date_range` 及 `notices`。`DateSelection` 仅为服务端日期辅助契约；查账工具继续接收明确的起止日期。

| kind | 补充参数 | 实际区间 |
| --- | --- | --- |
| `today` | 无 | 今天 |
| `yesterday` | 无 | 昨天 |
| `this_week` | 无 | 本周一至今天 |
| `last_week` | 无 | 上周一至上周日 |
| `last_n_days` | `days` | 包含今天，向前共 N 个自然日 |
| `this_month` | 无 | 本月一日至今天 |
| `last_month` | 无 | 上一个完整自然月 |
| `this_year` | 无 | 本年一月一日至今天 |
| `last_year` | 无 | 上一个完整自然年 |
| `month` | `month: YYYY-MM` | 指定月份，当前月截至今天 |
| `range` | `dateRange` | 用户指定范围，按下面的未来日期规则处理 |

不同 kind 不能夹带不适用的参数，缺少必需参数时拒绝，不猜测用户意图。任意自然语言到 kind 或具体日期的转换由后续模型规划实现。

统一规则：

- 区间包含首尾，默认最多 366 天，读取 `CHAT_V2_MAX_RANGE_DAYS`；闰年全年可查询。限制按实际执行区间计算。
- 开始日期在未来时拒绝；只有结束日期在未来时截到业务今天，并在 `notices` 中说明原请求发生了截断。调用方必须展示实际范围和调整说明。
- 跨年、闰年、周一/周日和短月份使用日历运算，不使用固定天数替代自然月。
- 返回的实际范围用于查询和保存，输入对象不被原地修改；不能展示原始未来结束日期却查询另一个区间。
- 日期结构错误由 Pydantic 拒绝；超长区间、全部未来及不支持的比较语义返回中文 `ServiceError(422, ...)`。

固定今天为 2026-10-27 时，最近七天为 2026-10-21 至 2026-10-27，本周为 2026-10-26 至 2026-10-27，上周为 2026-10-19 至 2026-10-25。

## 2. 比较区间

`resolve_comparison_range(current, ComparisonSelection, today=..., max_days=...)` 先规范化本期，再生成明确的上期区间：

| kind | 行为 |
| --- | --- |
| `previous_period` | 本期之前、相邻且等长的区间。例如 10-21 至 10-27 对比 10-14 至 10-20 |
| `previous_month_same_period` | 同一自然月内的本期映射到前一个月的对应日期，超出上月天数时截到月末 |
| `previous_month_full` | 本期起始月份之前的完整自然月 |
| `range` | 必须附 `dateRange`，使用用户明确的比较范围 |

本期跨月时，不自动猜测“上月同期”，要求改用前一等长区间或明确比较日期。映射到短月后两段时长可能不同，后续结果卡必须展示两段真实日期；不能声称它们严格等长。

`FilterQueriesV2.prepare_compare(book_id, CompareArgs)` 返回本期、上期两个 `PreparedFilter`。除日期外，两期分类、关键词、账户、记账人、金额、星期和排除 ID 完全相同，每个区间分别执行跨度限制。两期统计及差额的输出在第 03 步实现。

## 3. 筛选规范化与组合规则

`normalize_filter()` 会重新校验结构、检查分类与收支类型、规范化日期、去重，并返回新对象及日期调整说明。

- 不同字段之间取 AND；多个分类、账户、记账人或星期各自取 OR，空列表表示该字段不限。
- 分类只允许现有支出/收入分类；`entryType=both` 可使用两类分类。后续汇总必须分开收支，不将它们相加称为消费。
- 分类、账户和关键词去掉首尾空白；纯空白项直接拒绝，不能丢弃后变成不限条件。
- 分类、账户、UUID 按值去重；账户仍精确匹配保存的文本，不做模糊扩展。
- 金额以整数分处理，不接受浮点、布尔或数字字符串，边界为 0 至现有单笔上限 `MAX_CENTS`。是否包含上下限必须为 JSON 布尔值。
- 相同金额上下限要求两边都包含，否则拒绝空区间；不同上下限之间没有实际记录时是正常零结果。
- ISO 星期 1—7，周末为 `[6,7]`；SQL 内转换为 PostgreSQL DOW 的周日 0，不依赖进程或数据库会话时区推算账单日期。

关键词规则：

- 只搜索账单备注；`include` 的 `mode=any` 为任一包含，`mode=all` 为全部包含。
- `exclude` 任一命中即排除；无包含词时仍然应用排除词。
- 使用不区分大小写的字面子串匹配；ASCII 英文大小写已在 SQLite 和 PostgreSQL 验证，其他字符大小写遵循数据库规则。重复词按小写形式去重，并保留首个词项的展示形式。
- 使用 SQLAlchemy `icontains(..., autoescape=True)`，将 `%`、`_`、默认转义符 `/` 作为字面字符处理，反斜杠也不展开为查询表达式。参数独立绑定，不拼接用户 SQL。[SQLAlchemy 说明](https://docs.sqlalchemy.org/en/20/core/sqlelement.html#sqlalchemy.sql.expression.ColumnElement.icontains)
- 本步不做同义词猜测或分类扩展；后续如加入扩展，需要保留实际词项及匹配依据。

## 4. 权限和引用

`FilterQueriesV2.prepare()` 先调用现有 `LedgerService.require_book()`，再校验筛选与引用。生成的每个 SQL 都包含当前账本 ID、`LedgerService.access_condition()` 和有效账单条件。

- 个人账本只允许按所有者作为记账人筛选；共享账本的记账人必须在当前账本成员记录中。
- 已退出或移出的成员，其历史账单仍可由当前有权限的成员查询；这不会恢复前成员的账本访问权。展示必须称为“记账人”，不推断实际付款人。
- 排除的账单 ID 必须是当前账本内未删除且可访问的真实账单，可以在本轮日期范围以外；不能使用同账号其他账本、其他账号或已删除账单的 ID。
- 不存在和无权访问的账单统一返回相同 404 文案，不暴露其是否存在。
- 查询执行时仍包含权限条件，准备后成员被移除也不会读出记录。后续工具服务需在返回统计和保存消息前重新校验权限，不能把权限变化造成的无记录当作真实零消费。
- 模块不接收模型生成的用户身份或 SQL，不修改账单，也不提交事务。复用 `require_book()` 的短期账本锁，调用方必须在模型等待之前结束数据库事务。

## 5. 第 03 步调用方式

以下为服务端代码示例，`ledger/settings/book_id` 均由鉴权和服务依赖提供：

```python
from app.schemas.chat_v2 import DateSelection, EntryFilter
from app.services.analysis_v2 import FilterQueriesV2, resolve_date_range
from app.services.chat import business_today

today = business_today()
resolved = resolve_date_range(
    DateSelection(kind="last_n_days", days=7),
    today=today,
    max_days=settings.chat_v2_max_range_days,
)
queries = FilterQueriesV2(ledger, settings, today=today)
prepared = await queries.prepare(
    book_id,
    EntryFilter(
        date_range=resolved.date_range,
        category_ids=["coffee", "food"],
        accounts=["微信"],
    ),
)
# 查账工具按需要添加稳定排序、分页或基于 conditions 生成汇总语句。
statement = prepared.entries_statement()
notices = resolved.notices + prepared.notices
# 结果范围使用 prepared.filters.date_range，提示同时保留 notices。
```

`PreparedFilter.conditions` 可以用于只读汇总，但必须维持 `entries JOIN books` 以及全部条件，不能剥离权限谓词。`entries_statement()` 不添加分页或执行查询，工具的分页、统计卡、事实引用及输出上限在第 03 步实现。第 02 步不承诺已经支持自然语言自由问答。

2026-10-28 第 03 步已实现上述[工具层](chat-v2-tools.md)，新增代码请优先通过 `ChatToolsV2.execute()` 调用，以复用输出、分页、权限复核和事务清理规则。

## 6. 验证

从 `backend` 目录执行离线检查：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_analysis_v2_dates.py tests/test_analysis_v2_queries.py tests/test_chat_v2_contract.py -q
```

- 日期检查覆盖相对日期、明确月份、跨年、闰年、星期边界、短月比较、未来截断、范围限制和错误输入。
- 查询检查在内存 SQLite 实际执行同一套 ORM 条件，覆盖组合、包含/排除、通配符和转义符字面匹配、SQL 注入输入、零结果、金额边界、整数聚合、引用验证及共享权限变化。SQLite 只创建所需表，不模拟 PostgreSQL 部分索引及行锁语义。
- PostgreSQL 方言编译检查确认输入作为参数绑定；另提供显式开发库验证，不将内存验证当作真实 PostgreSQL 行为验证。

显式开发库验证：

```powershell
$env:MOJI_RUN_INTEGRATION_TESTS = '1'
try {
    .\.venv\Scripts\python.exe -m pytest tests/test_analysis_v2_integration.py -q
} finally {
    Remove-Item Env:MOJI_RUN_INTEGRATION_TESTS
}
```

2026-10-28 已执行真实开发库验证，通过 PostgreSQL 的关键词、通配符、星期、组合、整数汇总、比较与引用权限检查。测试仅创建事务内临时用户、账本及账单，结束后回滚，并逐一确认本次用户和账单均无残留。未连接正式库、Redis 或模型。

统一检查仍使用仓库根目录 `.\scripts\check.ps1`，默认关闭真实集成与模型开关，新增测试由 pytest 自动发现，不需要新增前端脚本。
