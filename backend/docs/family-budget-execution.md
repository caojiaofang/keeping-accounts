# 家庭预算：账单关联与执行汇总

第 11 步已完成[场景联调与回归验收](family-budget-regression.md)：迁移、原 32 项数据库 API 和新增前端 HTTP 联调共 34 项通过。原有步骤的验证记录保留时间范围，当前下一步为第 12 步设备验收与发布交接。

2026-11-06，第 06 步完成。新增 `app/services/budget_execution.py`，接入账单唯一关联、取消／换项、派生 use、原账单变更同步、月度汇总与分页查询。同步 Python／TypeScript 契约；复用 `0006_budget`，没有新迁移。

需求及交接见[总计划](../../docs/家庭预算功能与分步开发计划.md)，规则见[正式契约](family-budget-contract.md)，前置能力见[方案](family-budget-plan.md)、[月份](family-budget-months.md)、[资金与目标](family-budget-funds.md)及[存储](family-budget-storage.md)。本步只完成本地代码和隔离 PostgreSQL 验证，开发／正式库未升级，页面和线上服务未发布。

## 1. 账单与资金只归集一次

- 一笔有效支出最多关联一个叶子用途，整笔归集；收入、已删除或其他账本的账单不能新增关联。
- `expense` 按账单发生月的已确认快照或合法来源校验用途。`reserve/saving` 使用稳定用途，可以处理归档用途的真实缴费，不要求当月仍安排该项计划。
- 关联日期不能早于用途启用或有效期初，不能晚于北京时间今天。原记账接口允许的正常修改仍可保存；修改导致关联不合法时自动清除关联，不用预算规则阻止真实账单修正。
- 关联 `reserve/saving` 时，由服务端从原账单读取金额、日期、备注和 entryId 生成一次 use；余额允许为负。客户端不能直接提交另一份支付金额。
- 设置预算、月预留 520、月储蓄 4,200 和实际存入都不增加消费。月支出始终只统计原有 LedgerEntry；use 不作为第二份消费。
- 储蓄 use 可显式指定同项目的 goalPeriodId，也可不指定。允许在目标截止后支付，包括已关闭期的迟到用款；日期不能早于目标开始日。
- 退款仍按原规则记录为收入，不自动减少预算消费或恢复储备。需要纠正错误支出时，明确修改、删除原账单或取消关联。

合法关联按需固化缺失月快照，复用第 04—05 步内部方法，和关联／use 一起提交。已有快照不改；归档资金用途在该月无计划时不制造预算。读取和汇总均不落快照。

## 2. 关联接口与版本

统一前缀 `/api/v1/books/{book_id}/budgets`，需要既有 Bearer 鉴权。

| 接口 | 请求／响应 |
| --- | --- |
| GET `/entries/{entry_id}/link` | 返回当前账单版本、关联和 linkVersion；未关联为 0，取消后保留版本 |
| PUT `/entries/{entry_id}/link` | `{requestId, entryVersion, linkVersion, itemId, goalPeriodId?}`，建立或更换唯一关联 |
| DELETE `/entries/{entry_id}/link` | JSON 正文 `{requestId, entryVersion, linkVersion}`，取消关联并恢复派生使用 |

三种响应均为 `BudgetEntryLinkState`：`schemaVersion/bookId/entryId/entryVersion/linkVersion/link/fundVersions/notice`。link 含 `entryId/bookId/itemId/goalPeriodId/version/isDeleted/updatedAt/updatedBy`；fundVersions 列出本次余额受影响项目的最新资金版本，查询及未变更时可为空。

读取 linkVersion 后再提交，不能在取消后把版本重新设为 0。entryVersion 防止用旧账单金额关联，linkVersion 防止另一设备的换项或取消被覆盖。关联本身不递增账单版本；正常账单编辑会递增账单版本，并递增当前有效关联版本，即使仅改备注也保留同步审计。

同项目、同目标、同一份未变化账单的重复设置不新增 use、不递增关联或资金版本。使用同 requestId 重试先复核当前权限，再重放原成功结果；请求摘要包含路径 entryId 和规范化正文。同 requestId 换账单／操作／内容返回 409，不能凭旧幂等结果绕过退群或编辑权限。

取消不会物理删除关联：保留 isDeleted 和单调版本，作废有效 use。重新关联继续递增版本。换资金用途时先恢复原项目，再扣新项目，两个资金版本分别加一；换到 expense 时只恢复旧资金。可用金额不足仍记录真实用款，不伪造存入。

关联示例（版本和 UUID 需替换为实际读取结果）：

```json
{
  "requestId": "61000000-0000-4000-8000-000000000001",
  "entryVersion": 1,
  "linkVersion": 0,
  "itemId": "62000000-0000-4000-8000-000000000001",
  "goalPeriodId": null
}
```

取消示例：

```json
{
  "requestId": "61000000-0000-4000-8000-000000000002",
  "entryVersion": 1,
  "linkVersion": 1
}
```

尚未建立家庭预算的账本可以读取未关联状态；关联写入要求已有预算方案根。跨账本对象为 404，当前成员无账单编辑权为 403，版本／幂等冲突为 409，非法关联条件或字段为 422；不回显伪造的金额或身份。

## 3. 原记账入口的同步

既有 `PUT /api/v1/entries/{entry_id}` 和 `DELETE /api/v1/entries/{entry_id}` 在原 LedgerService 提交前调用 `sync_entry`。内部同步不提交事务，也不绕过原账单权限；账单、关联、派生 use、项目资金版本、必要快照和共享活动保持原子一致。

| 原账单变化 | 预算处理 |
| --- | --- |
| 改金额／日期／备注，关联仍合法 | 旧 use 作废，按新账单建立替代 use；同项目保存 replacesId，关联版本加一 |
| 日期早于目标开始，但用途仍合法 | 清除目标归属，保留用途和有效 use；返回重新选择目标期提示 |
| 日期晚于目标截止 | 允许保留明确选择的目标期，不把迟到支付强行归入新一期 |
| 改为收入、日期早于用途／期初、晚于今天、expense 发生月没有该用途 | 取消已不合法关联并作废 use，正常账单修改仍成功 |
| 转到另一合法个人账本 | 取消原关联、恢复原资金；当前账本可按保留的 linkVersion 建立新关联 |
| 删除 | 作废关联与 use，恢复对应资金；重复删除不重复恢复 |

共同账单跨账本仍按原规则拒绝。转移后的旧预算路径无法读取该账单；在新账本查询时返回当前 linkVersion，隐藏其他账本的旧关联明细。重新关联保留版本，不把旧墓碑当成从未关联。

原更新响应增加可选 `budgetNotice`，仅本次发生自动清除／目标失效时返回中文原因；正常响应不增加非空提示。该提示不写进账单备注，也不当成目标定义。前端第 07 步需展示提示并刷新关联、月度执行和相关项目；当前尚未接入提示 UI。

已关闭目标的设置继续固定，实际用款编辑／删除仍按真实记录重算其历史使用与缺口。不能因为目标已关闭而留下错误扣减，也不通过修改 use 生成第二份还贷账单。

## 4. 执行汇总及分页

| 接口 | 参数／响应 |
| --- | --- |
| GET `/months/{month}/execution` | `{schemaVersion, bookId, month, generatedAt, budget, expenseTotal, linkedExpenseTotal, unlinkedExpenseTotal, items}` |
| GET `/months/{month}/entries` | `itemId` 或 `unlinkedOnly=true` 可选且互斥；`beforeEntryId` 可选，`limit=1..100` 默认 20；返回 `BudgetEntryPage` |
| GET `/items/{item_id}` | 扩展既有详情，增加 `entries/nextEntryCursor`；`beforeEntryId` 可选，`entryLimit=1..100` 默认 20；原资金流水分页参数继续独立使用 |

budget 为原月份响应，仍区分 not_set／preview／confirmed；未设置月份也可以如实返回该月全部未关联支出，不套用当前方案。expenseTotal 统计该月有效支出账单，linkedExpenseTotal + unlinkedExpenseTotal = expenseTotal，收入与删除项不计入。

每个 items 含 `itemId/kind/name/groupId/inPlan/plannedAmount/expenseAmount/remainingAmount/depositedAmount/usedAmount/availableAmount/fundVersion`：

- 按该月 resolved plan 返回计划；当月有实际归集或存入／使用但不在计划中的归档用途也保留，inPlan=false、plannedAmount=0。
- expenseAmount 来自原账单；expense 的 remainingAmount=月额度−关联实际支出，允许为负。reserve/saving 的 remainingAmount=null，不把储蓄计划说成可消费额度。
- depositedAmount 和 usedAmount 是所选月中不晚于业务今天的有效流水，期初不算本月存入。availableAmount 是截至业务今天的项目累计可用资金，与详情一致，不是所选历史月份期末余额；expense 时为 null。
- 正常记账保存的未来日期账单仍按原发生月纳入支出统计，但未来日期不能新增资金关联，不伪造提前支付。

账单分页仅返回有效支出，按日期、UUID 降序。`BudgetEntryPage={schemaVersion,bookId,entries,nextCursor}`，每项为 `{entry,link,linkVersion}`，entry 复用现有账单响应；取消后的 tombstone 保留版本，unlinkedOnly 包含该类支出。游标必须仍属于当前月／项目／未关联筛选，失效后返回 404，客户端重新读取第一页。项目详情跨月分页只列当前仍关联该用途的有效支出，作废 use 的历史在原资金流水中追溯。

## 5. 权限、并发与事务

有效共同成员可读全部共同预算及支出；只有原账单创建人或当前账本创建者可关联／取消／编辑。成员可通过自己有权编辑的支出产生受控 use，仍不能手工登记存入或修改共同目标。

账本行锁先于项目锁，跨个人账本沿用既有 UUID 顺序；所涉项目按 UUID 顺序加锁。读请求持有账本共享锁，使多次查询处于同一写入边界；写请求在等待账本锁后再次复核成员状态。源账单发生转移或删除时重新检查，不凭加锁前的内容继续写入。

同请求并发只生成一次有效 use；不同关联请求使用相同旧版本时最多一个成功。与旧记账删除并发时，无论关联先完成还是删除先完成，最终均没有有效扣减。SQL 异常回滚账单及所有派生写入，预算和账本接口分别返回脱敏 503；原成功幂等凭据不会由失败操作留下。

## 6. 验证与复现

- 定向离线：`pytest tests/test_budget_execution_contract.py tests/test_budget_api.py tests/test_budget_contract.py tests/test_ledger.py -q --tb=short`，**92 passed**，包含严格版本、伪造字段拒绝、路径／正文绑定、DELETE 正文和分页参数。
- 新增本机真实 API／SQL 测试 **12 项**：公共缴费与还贷、50,400／50,000／400 续建、闭期修正／删除、收入退款口径、历史月份、日期失效、换项、跨个人账本、分页／归档实际存入、成员权限、等待锁后退出／被移出、并发及故障原子回滚。
- 与第 03—05 步联合数据库回归：**32 passed**（本步 12 + 既有 20）。仅 token 到测试用户解析使用替身，其余鉴权规则、API、SQL 和独立事务实际执行；不使用远程开发库、Redis、模型或微信服务。

先按[资金验证说明](family-budget-funds.md)建立只绑定本机回环地址的专用 PostgreSQL 17 容器。本次端口为 5340、数据库固定为 `moji_budget_migration_test`；以下连接仅指该一次性测试环境。

```powershell
$budgetPreviousFlag = $env:MOJI_RUN_INTEGRATION_TESTS
$budgetPreviousUrl = $env:MOJI_BUDGET_MIGRATION_TEST_URL
try {
    $env:MOJI_RUN_INTEGRATION_TESTS = '1'
    $env:MOJI_BUDGET_MIGRATION_TEST_URL = 'postgresql+asyncpg://budget_test:budget-local-test-only@127.0.0.1:5340/moji_budget_migration_test'
    .\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_budget_execution_integration.py tests/test_budget_funds_integration.py tests/test_budget_months_integration.py tests/test_budget_plan_integration.py -q --tb=short
    if ($LASTEXITCODE -ne 0) { throw '预算账单执行数据库回归失败' }
}
finally {
    $env:MOJI_RUN_INTEGRATION_TESTS = $budgetPreviousFlag
    $env:MOJI_BUDGET_MIGRATION_TEST_URL = $budgetPreviousUrl
}
```

最终统一入口 `.\scripts\check.ps1` 通过：前端全部检查、管理端 TypeScript、后端 **652 passed、47 skipped**、Ruff、格式与 `pip check` 均通过。真实集成默认跳过，上述预算 API 32 项已单独实际执行；保留既有 Starlette 依赖弃用提示。没有发行构建或设备验收。

测试在随机 schema 迁移并自动清理，专用容器验证后移除；环境记录见[总计划交接](../../docs/家庭预算功能与分步开发计划.md)。原账单服务现在需要预算表参与同步，实际部署须先升级到 `0006_budget` 再启用本代码；仓库 head 不能证明开发／正式库已升级。

## 7. 前端交接

新增独立前端 API、store 和隔离体验数据，接入账号／账本／月份切换、错误与幂等重试。保存请求绑定 entryVersion/linkVersion，冲突时读取新状态；读取取消状态仍保留 linkVersion，个人账本转移后不能回到 0。

账单编辑返回 budgetNotice 时展示原因并刷新执行及项目余额。关联结果 fundVersions 只列受影响资金用途，不能据空数组判断账单未归集；expense 用途没有用途资金扣减。月度 budget 只负责计划状态，实际消费／存入／资金分别展示，历史月份查看时不要把截至今天的 availableAmount 标作历史期末。

原记账输入无需增加身份或另一份支付金额；从正常记账返回后通过本步接口明确关联。第 08—10 步再提供表单、总览和执行界面，第 11—12 步补联调／设备与发布验收，本次不提前实现页面。

第 07 步已按以上约定接入[前端服务与状态](../../frontend/docs/family-budget-state.md)，17 项预算检查通过；第 08 步开始页面设置。上述第 06 步数据库验证记录不代表本次重跑。
