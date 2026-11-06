# 家庭预算：月度预览、确认与单月调整

2026-11-06，第 04 步完成。新增 `app/services/budget_months.py`，在既有预算 router 中接入月份与精确均摊操作，补齐 Python／TypeScript 契约。没有新增数据库迁移，仓库 head 仍为 `0006_budget`。

需求与交接见[总计划](../../docs/家庭预算功能与分步开发计划.md)，前置能力见[方案接口](family-budget-plan.md)及[存储说明](family-budget-storage.md)。本步在本机临时 PostgreSQL 验证，开发库和正式库未升级、未发布，页面后续接入。

## 1. 接口与权限

前缀 `/api/v1/books/{book_id}/budgets`，复用现有 Bearer 身份、账本锁及成员复核。

| 方法与路径 | 请求 | 响应 |
| --- | --- | --- |
| GET `/months/{month}` | 合法 YYYY-MM 路径，无请求体 | `BudgetMonthResponse` |
| POST `/months/{month}/confirm` | `ConfirmBudgetMonth`：requestId、sourceRevisionId、planVersion | `BudgetMonthResponse` |
| PUT `/months/{month}` | `UpdateBudgetMonth`：requestId、version、override | `BudgetMonthResponse` |
| POST `/equalizations/preview` | `PreviewBudgetEqualization`：version、itemId、totalAmount、startMonth、endMonth | `BudgetEqualizationPreview` |
| POST `/equalizations` | 上述均摊输入，加 requestId、impactToken | `BudgetEqualizationResponse`，包含月份列表 |

有效成员可读共同账本的月份，只有创建者可确认、改预算或均摊。越权账本统一 404，有效普通成员写入为 403。所有成功响应和业务错误使用 no-store；沿用既有 401／409／422／503 中文错误及数据库异常脱敏和回滚。

月份路径与字段只允许 2000-01 至 2100-12，金额使用严格整数分，不接受身份、可信汇总或客户端计算的均摊 schedule。

## 2. 月份状态与版本

| state | 返回内容 | 是否创建记录 |
| --- | --- | --- |
| not_set | 无有效方案，plan／totals／sourceRevisionId／snapshotId 为 null，version=0 | 否 |
| preview | 按该月有效 revision 解析，返回计划及汇总；snapshotId=null、version=0 | 否 |
| confirmed | 返回已保存快照及单月覆盖，snapshotId 有值、version 从 1 起 | 已存在 |

`planVersion` 是读取时方案根的当前版本；`version` 是月快照自身版本。确认、单月编辑、均摊都不递增方案根版本。

GET 先查该月快照，存在则始终返回快照；不存在时，选择生效月份不晚于查询月的最后一个当前有效 revision。首次启用前返回 not_set，不用今天的方案补造过去预算。

因此，未打开过的历史月份仍能按当时的有效版本还原。已确认月份则保留原始来源，即使它后来被同月新 revision 取代，也不改绑或重算基础方案。

快照读取校验 `base_plan + override = resolved_plan`，并复用第 01 步纯计算重算汇总。发现持久化内容不一致时返回脱敏的 503，不将损坏数据解释为零预算。

## 3. 确认与历史规则

客户端从 GET 的预览复制 sourceRevisionId 和 planVersion 后确认，例如：

```json
{
  "requestId": "40000000-0000-4000-8000-000000000101",
  "sourceRevisionId": "50000000-0000-4000-8000-000000000001",
  "planVersion": 1
}
```

上述 UUID 为契约示例，实际来源必须复制服务端返回值。

- 确认在账本排他锁下检查根版本和来源。来源已变更或预览版本过期返回 409。
- 新月份保存一份唯一快照，包含当时基础方案、空覆盖和解析结果，version=1。
- 已确认月份若预期来源相同，返回原快照，不改额度或递增月版本；不同来源返回 409，不能借重新确认覆盖已有单月调整。
- 可以固化过去已经启用月份的原计划；确认请求不能携带自定义额度。启用前仍禁止建快照，已过去月份不能 PUT 改预算。
- 同 requestId 原样重试返回原成功响应，即使之后跨月、根版本变化或该月又有新修改。它不是最新查询，客户端恢复后如需继续编辑应重新 GET。

`create_snapshot` 是后续执行服务可复用的不提交事务的内部方法；调用方必须持有账本排他锁，并先完成对应权限、月份存在性和合法来源检查，不能把任意客户端方案交给它固化。

## 4. 单月覆盖

PUT 必须针对已确认且处于当前或未来的月份，携带最新月版本。不存在快照或版本冲突返回 409。

```json
{
  "requestId": "40000000-0000-4000-8000-000000000102",
  "version": 1,
  "override": {
    "plannedIncome": null,
    "itemAmounts": [{"itemId": "30000000-0000-4000-8000-000000000014", "amount": 60000}]
  }
}
```

override 完整替换原覆盖，始终应用在快照的原始 base_plan 上：

- plannedIncome=null 恢复继承，0 是有效收入计划；item amount=0 是有效预算额度。
- 清空 itemAmounts 恢复对应基础额度，空 override 恢复全部继承。
- 只能调整该快照已有叶子项目，未知项目返回 422，重复项目在 schema 层拒绝。
- 不能借单月编辑新增项目、改性质或分组。改变结构仍使用第 03 步方案版本设置。
- 每次成功新修改递增月 version，不改方案默认值，也不影响其他月份；使用旧版本的新请求被拒绝。
- 已成功请求在新月原样重放不会再写入，允许返回原结果；新的请求仍不能修改过去预算。

待分配可以为负，超出收入的计划仍可保存，实际资金和账单均不因此变化。

## 5. 精确均摊与原子保存

```json
{
  "version": 1,
  "itemId": "30000000-0000-4000-8000-000000000004",
  "totalAmount": 610000,
  "startMonth": "2026-11",
  "endMonth": "2027-10"
}
```

version 是方案根版本。批量接口单次最多 **120 个连续月份**，包含首尾，不能从过去月份开始；纯计算函数仍可处理既定完整日期范围。totalAmount 可为 0，表示把窗口内所选项目额度明确设为 0。

预览返回 impactToken、expiresAt、schedule 和 affectedMonths，不创建月快照。金额用整数商和余数分配：610,000 分分 12 月，前 4 月 50,834，后 8 月 50,833，合计不丢分。

采用时给相同输入加 requestId 和 impactToken，POST `/equalizations`：

1. 在账本锁内先验当前权限，再查成功幂等记录。
2. 校验预览所属操作者、账本、操作、正文摘要及 10 分钟有效期。
3. 重算根版本、北京时间业务月和每个月的快照 ID／月版本／来源。任一依赖变化返回 409，不能部分采用旧预览。
4. 全窗口校验所选项目存在：已确认月份使用冻结的基础方案，未确认月份使用该月有效 revision；缺少预算或项目返回 422。
5. 新月份保存带最终覆盖的快照，version=1；已有月份仅替换所选项目额度，保留收入和其他项目覆盖，月 version 加一。
6. 一次提交所有月记录、预览消费、幂等响应、状态同步及共同账本活动；中途失败整体回滚。

均摊不改原方案月额。采用 6,100 元精确分配后，原先每月 520 元的默认值仍用于窗口外；每月差额留在待分配中，不自动挪给其他用途。均摊、确认和覆盖均不创建支出或存款流水。

## 6. 验证与后续交接

离线定向命令（backend 目录）：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_budget_month_contract.py tests/test_budget_contract.py tests/test_budget_api.py tests/test_budget_math.py -q
```

本次 **114 passed**，覆盖新增路径、120 月上限、非法金额与区间、禁止伪造 schedule／身份、0 值及既有方案与纯计算契约。

本机 PostgreSQL 测试复用[存储说明中的专用 Docker 测试库启动与清理流程](family-budget-storage.md)，显式设置 MOJI_RUN_INTEGRATION_TESTS 和 MOJI_BUDGET_MIGRATION_TEST_URL，测试目标为：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_budget_months_integration.py tests/test_budget_plan_integration.py -q
```

本次 **13 passed**，包括 8 个月度用例和 5 个既有方案用例回归。真实 API／SQL／独立事务，只有登录令牌解析模拟，不访问开发库、Redis、微信或模型。

重点验收：三种状态、未启用历史、未打开历史还原、过期来源、快照冻结、完整替换与 0 元、跨月成功重放、精确跨年均摊、其他覆盖保留、token 过期与依赖变更、末月缺项目、并发确认与修改、共享只读，以及故障注入到第二个月时首月写入也被回滚，原 token／requestId 可重试。

统一入口 `.\scripts\check.ps1` 已通过：前端全部检查、管理端 TypeScript、后端 **599 passed、28 skipped**、Ruff 和 `pip check`。新增数据库用例在统一入口默认跳过，已通过上述单独启用命令执行。仍有既有 Starlette 测试依赖弃用提示。

复用预算幂等表的操作名为 month_confirm、month_update、equalize；月份写入摘要包含路径月份，防止同 requestId 换月重放。根方案与月份共用幂等助手，原方案接口回归通过。

第 05—06 步的[预留、储蓄与目标](family-budget-funds.md)及[账单执行](family-budget-execution.md)已完成；执行记录不随时间或预算确认自动增加，实际资金／关联写入在已有操作权限和账本锁内固化合法必要月份。计划快照固定，实际账单修正仍更新执行。下一步第 07 步接入前端服务、状态和隔离体验数据。
