# 聊聊花费 V2：只读查账工具与事实输出

2026-10-28，第 03 步交付。代码位于 `app/services/chat_v2_tools.py` 和 `app/services/chat_v2_cursor.py`，复用第 02 步[日期与组合筛选](chat-v2-filters.md)。本步未注册 V2 API、接入模型编排或修改小程序界面，尚未部署。

## 1. 调用入口与职责

`ChatToolsV2` 只接收服务端提供的账本与身份，入口为 `execute(call_id, book_id, name, arguments)`。工具注册表 `ARGUMENTS` 固定名称及 Pydantic 参数模型；不允许模型指定用户 ID、账本 ID、SQL 或任意 Python 方法。

| 工具 | 参数模型 | 结果 |
| --- | --- | --- |
| `summarize_entries` | `SummaryArgs` | 全部匹配记录的总额与笔数，支持分类、日期、账户、记账人分组 |
| `search_entries` | `SearchArgs` | 全部匹配统计、当前页真实账单、匹配依据及短期游标 |
| `compare_periods` | `CompareArgs` | 两期金额/笔数、差额、可计算时的变化率与分组贡献 |
| `get_entry` | `EntryArgs` | 当前有权访问的单笔有效账单 |

调用示例：

```python
from app.services.chat import business_today
from app.services.chat_v2_tools import ChatToolsV2

runner = ChatToolsV2(ledger, settings, today=business_today())
result = await runner.execute(
    "q1",
    current_book_id,
    "summarize_entries",
    {
        "filters": {
            "dateRange": {"start": "2026-10-21", "end": "2026-10-27"},
            "entryType": "expense",
            "categoryIds": ["food", "coffee"],
        },
        "groupBy": "category",
    },
)
```

日期示例仅用于说明，实际日期由受控模型计划及服务端日期校验提供。`call_id` 由编排器生成，要求英文字母开头，只包含字母、数字、下划线和短横线，最长 32 字符，并在一轮中唯一。上游模型原生 call ID 在适配层另外关联，不能直接用任意长 ID 作为事实前缀。

工具运行前校验参数和账本权限，返回前再次校验权限，结束时回滚只读事务以释放短期锁。调用方不能在同一事务中夹带待提交的业务写入。取消向上传播并完成事务清理，不自动重试数据库或付费模型请求。

## 2. 统计与结果一致性

- 金额使用整数分，聚合总额不受单笔金额上限限制；笔数来自全部匹配记录。
- `entryType=both` 的支出与收入分别生成事实，不把收入和支出相加称作消费，不自动将退款抵扣支出。
- 汇总通过一条分组 SQL 得到全部金额和笔数，再生成统计卡；不另发一条 SQL 拼凑总额。
- 搜索用 CTE 在同一条 SQL 内读取全部匹配统计和当前页，翻页条件只作用于明细，不缩小总额与笔数的统计范围。
- 比较通过一条 `UNION ALL` 查询读取两期分组统计，同一张卡内部来自同一个业务查询快照。
- 查询之后的账单变动不会改写已生成事实；后续工具调用重新查询。跨调用、跨页不承诺冻结整个账本快照。
- 查询零条属于成功且输出零总额和零笔数；权限失效、存储错误属于失败，不伪装成零记录。

日期分组按时间顺序输出有匹配记录的日期，最多受 366 天范围限制；未列日期没有匹配记录，不推断实际没有消费。其他分组最多单列金额最高的二十组，再将其余分组合为一行，金额与笔数仍完整。

合并行使用 `isOther=true`、`groupKey=null`，不会与真实账户名“remaining”等词冲突；后续前端不能把合并行误当成一个真实分类或账户直接筛选。

## 3. 比较与贡献

- 本期采用 `filters.dateRange`，上期采用 `previousRange`，其他筛选完全一致。
- 绝对差额为“本期减上期”，正负值都保留。
- 变化率为差额除以上期金额；上期为零时不产生该事实，并在卡片说明中明确原因。
- 分组贡献按各组的本期减上期计算，包含只在一边存在的分类，所有组差额之和等于总差额。
- 分组展示按差额绝对值排序，最多二十组，其余合并；按日期分组时按相对起始日对齐，例如本期第一天与上期第一天，并展示对应日期。周期长度不同时明确无对应日期。
- 总差额不为零时提供“该组对总差额的贡献比例”，它可以为负或超过 100%；不把它称为普通消费占比。总差额为零时省略贡献比例。
- 百分比统一使用整数基点：10000 表示 100%，采用 `Decimal` 的 `ROUND_HALF_UP`，不使用浮点累计。四舍五入后比例之和可能略有差异。
- 差额贡献只表示账单数值来源，不作为现实消费原因的证明。

## 4. 分页规则与配置

新增可选配置 `CHAT_V2_CURSOR_SECRET`，用于分页 HMAC-SHA256 签名，至少 32 字符，各环境独立。不得复用邀请密钥、模型密钥或管理员密码。密钥通过环境注入，不提交仓库、不返回客户端、不写日志。

本步 V2 路由尚未启用，未配置分页密钥不会影响 V1 或应用启动；正式启用 V2 分页前必须配置。需要下一页却缺少密钥时返回明确的服务不可用错误，不悄悄截成二十笔并声称完整。

- 每页最多二十笔，额外读取一笔判断是否还有下一页；最后一页 `nextCursor=null`。
- `date_desc` 按日期、时间、UUID 全部降序；缺失时间按空字符串处理，在同日排到已知时间之后。
- `amount_desc` 按金额、日期、UUID 全部降序。同日同金额依靠 UUID 区分位置。
- 使用键集分页，不使用 offset；上页最后位置决定下一页起点，账单不变时无重复和遗漏。
- 游标绑定账号、账本、规范化筛选、排序和页大小，查询列表条件在指纹中规范排序；换查询范围、换账号或账本均不能复用。
- 游标有效期 30 分钟，验证签名、版本、时刻及位置字段。篡改、过期或范围变化统一返回“分页信息无效或已过期，请从第一页重新查询”。密钥变更也使旧游标失效。
- 游标使用签名和 Base64 编码，不是加密数据；其中只放不透明范围指纹、分页位置和时刻，不放凭据或完整筛选正文。
- 每页重新读取当前账单及全量匹配统计。并发新增、删除或修改排序字段时，不保证跨页冻结快照，用户需要刷新第一页查看最新完整列表。

## 5. 事实、卡片和匹配依据

`ToolResult` 继续使用 `callId/name/status/cards/factRefs/nextCursor/error`，新增 `notices` 传递未来日期截断等说明。分页是有意的明细展示边界，成功分页仍为 `complete`。

每个事实均由工具生成：

- ID 以服务端调用编号为前缀，例如 `q1.expense.total`、`q1.expense.previous_total`、`q1.expense.difference`。其稳定性限于这次工具结果，后续调用使用新的前缀。
- 保留 `sourceCallId/unit/label/value`，新增 `metric/dateRange/previousRange/generatedAt`。上期事实绑定上期区间，差额及比例同时标识比较范围，时间使用服务端 Unix 毫秒。
- `metric` 区分总额、笔数、上期金额/笔数、差额、变化率、占比/贡献比例及单笔金额。精确正文仍须由第 06 步引用替换与校验。
- 提供本期和上期的起止日期事实，后续模型可通过占位符引用日期，不必自己生成数值。
- 卡片、行只能引用实际存在的事实 ID，事实不能冒用其他调用编号；重复事实或卡片 ID 直接拒绝。

账单行包含真实 `entryId/date/time/entryType/categoryId/account/creatorId`、金额和日期事实引用、`matchedKeywords` 及匹配说明。`creatorId` 表示记账人，不等同付款人；备注和标签仍是待防注入的数据。

比较卡必须有上期日期，趋势行必须有实际日期，账单行必须有真实 ID 及必要账单字段。`get_entry` 对其他账本、不存在、已删除和未来记录统一返回不泄漏存在性的错误。

## 6. 失败处理

已注册工具的参数错误返回 `ToolResult(status=error, error.code=invalid_arguments)`；账本或账单不可访问为 `not_found`，数据库异常为 `unavailable`。失败结果没有卡片、事实和下一页，不返回原始 SQL 异常或参数。

未注册工具或非法调用编号直接抛出中文 `ServiceError(422)`，供后续编排器处理。接口认证仍由业务 API 负责，本模块不生成 token 或决定用户身份。

## 7. 验证与后续任务

离线检查：

```powershell
# backend 目录
.\.venv\Scripts\python.exe -m pytest tests/test_chat_v2_tools.py tests/test_chat_v2_contract.py -q
```

覆盖四个工具、汇总/搜索/比较一致性、超过二十笔及同日同金额分页、缺失时间、游标范围/账号/账本/密钥/过期校验、退款、负差额贡献、零基期、真实 ID、权限失效、事务释放、取消、错误脱敏和分组截断。

2026-10-28 已在真实开发 PostgreSQL 执行 `test_chat_v2_tools_integration.py`，验证分组、CTE 搜索、两种键集分页、比较、单笔读取和退款分开统计，全部临时用户及账单在事务末尾回滚并核对无残留。测试不连接 Redis 或模型，不修改正式库。

```powershell
$env:MOJI_RUN_INTEGRATION_TESTS = '1'
try {
    .\.venv\Scripts\python.exe -m pytest tests/test_chat_v2_tools_integration.py -q
} finally {
    Remove-Item Env:MOJI_RUN_INTEGRATION_TESTS
}
```

下一步为第 04 步：保存和恢复可修正的查询上下文及结果引用；此步工具入口供后续编排复用，不提前启用模型循环或 V2 前端。
