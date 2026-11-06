# 家庭预算：预留、储蓄与目标执行

2026-11-06，第 05 步完成。新增 `app/services/budget_funds.py`，在既有预算 router 中接入用途发现、资金详情、期初／存入／修正及目标期接口；同步 Python 与 TypeScript 类型。复用现有 9 张预算表，没有新增或改写迁移，head 仍为 `0006_budget`。

需求、进度与交接见[总计划](../../docs/家庭预算功能与分步开发计划.md)，字段规则见[正式契约](family-budget-contract.md)，前置能力见[方案接口](family-budget-plan.md)、[月度预算](family-budget-months.md)和[存储说明](family-budget-storage.md)。本步完成本地服务及隔离数据库验证；前端页面、开发库迁移和线上发布仍未进行。

## 1. 实际资金口径

- `reserve/saving` 可登记实际资金；`expense` 不能手工登记资金。
- 项目当前可用 = 一次有效期初 + 有效实际存入 − 有效使用。只统计实际日期不晚于北京时间今天且未作废的记录，允许负余额。
- 月计划 520／4,200、确认预算、均摊及月份推进均不产生实际资金，也不创建收入或支出账单。
- 金额为正整数分，单笔不超过 999,999,999；0 不创建记录。派生合计在 JavaScript 安全整数范围内校验。
- 实际日期使用严格 `YYYY-MM-DD`，不能晚于北京时间今天，不能早于项目启用月或有效期初日期。期初不能晚于已有执行。
- 一个项目只有一条有效期初。有其他执行时不能直接作废期初，可用替代记录修正金额／日期。
- 修正先作废原记录、递增原记录版本，再新增同性质的替代记录并保存 `replacesId`；`replacement=null` 只作废。流水查询保留原记录和替代链。
- 手工写入仅接受 `opening/deposit`。`use` 必须引用真实支出，第 06 步已接入[公共关联、用款同步和月度执行汇总](family-budget-execution.md)，不提供任意提交支付金额的直接扣款接口。

存入时，如实际月份已有合法来源且该来源包含该项目，则在同一事务内固化缺失的月快照。已有快照不修改；归档用途的补记若无当月计划，不制造新预算。只读详情、目标创建和时间推进均不会暗中确认月份。

## 2. 查询与分页

以下后缀的统一前缀为 `/api/v1/books/{book_id}/budgets`，需要既有 Bearer 鉴权。

| 接口 | 参数／响应 |
| --- | --- |
| GET `/items` | `after=UUID` 可选，`limit=1..100`，默认 50；返回 `{schemaVersion, bookId, items, nextCursor}` |
| GET `/items/{item_id}` | `beforeRecordId=UUID` 可选，`recordLimit=1..100`，默认 20；返回 `{schemaVersion, bookId, generatedAt, item, balance, goals, records, nextRecordCursor}` |
| GET `/items/{item_id}/funding-preview?goalId=UUID` | 仅接受 `goalId`，返回 `BudgetFundingPreview`；拒绝客户端提交余额、已用金额或测算日期 |

用途列表包括当前、未来和归档用途，按 UUID 升序翻页，`nextCursor` 作为下一次的 `after`。项目含 `id/kind/status/enabledMonth/fundVersion/configuration`；`configuration` 是最近生效月份的历史方案配置，可能来自未来版本或已被替换的旧版本，用于发现用途和恢复名称，不能当作当前月额度。当前月预算以月份接口为准。

`status` 在读取时根据当前／未来有效方案和当前／未来已确认快照计算，避免跨月后仅依赖最近写入的缓存状态。归档保留 UUID、资金与目标，可补记或修正实际资金；创建新目标前须先在方案中恢复该用途。

资金流水按 `createdAt`、UUID 降序分页，游标必须属于当前账本和项目。分页包含作废记录供追溯，余额始终统计全部有效记录，不只统计当前页；目标列表含所有历史期。第 06 步已扩展独立关联账单分页 entries/nextEntryCursor，使用 beforeEntryId/entryLimit，不改变资金流水分页参数。

`balance` 字段为 `asOfDate/openingAmount/depositedAmount/usedAmount/availableAmount`。`generatedAt` 和审计时间使用毫秒时间戳，日期仍为业务自然日。

## 3. 写接口与版本

| 接口 | 请求 | 响应 |
| --- | --- | --- |
| POST `/items/{item_id}/funds` | `CreateBudgetFund` | `SavedBudgetFund` |
| POST `/items/{item_id}/funds/{record_id}/correct` | `CorrectBudgetFund` | `SavedBudgetFund`，record 为作废原记录，replacement 为新记录或 null |
| POST `/items/{item_id}/goals` | `CreateBudgetGoal` | `SavedBudgetGoal`；新建及续建使用同一接口 |
| PUT `/items/{item_id}/goals/{goal_id}` | `UpdateBudgetGoal` | `SavedBudgetGoal`；完整提交目标设置和 state |

两类写响应均含 `schemaVersion/bookId/itemId/fundVersion/balance`。资金响应另含 `record/replacement`，目标响应另含 `goal`。资金记录返回实际归属、版本、作废时间和操作者，客户端不能自行指定可信身份、目标归属或余额。

所有写入带 `requestId/fundVersion`，修正再带 `recordVersion`，目标更新再带目标 `version`。每次成功的资金或目标操作只递增项目资金版本一次，不递增方案根版本；执行时新建月快照仍是 version=1。存入自动归集到目标期、目标创建追认已有存入时，会递增发生归属变化的资金记录版本，客户端应重新读取记录后再修正。

个人账本拥有者／共享账本创建者可写，有效共同成员只读。操作先锁账本并复核当前权限，再检查成功幂等结果和版本；同请求重放原结果，不再次累加资金、版本、活动或快照。请求摘要包含路径项目、记录／目标 ID；同 requestId 更改正文或操作返回 409。权限转让后旧创建者不能凭成功请求重放绕过权限。

资金、目标、存入归属、必要快照、共享活动及幂等结果在一次事务提交。SQL 故障统一回滚并返回脱敏 503，原请求可以重试；外部／不存在对象统一 404，合法成员的只读写入为 403，版本／业务依赖冲突为 409，非法字段／日期为 422。

以下 JSON 分别展示独立操作的契约格式，版本须使用实际读取结果，示例 UUID 不用于真实账号。

登记存入：

```json
{
  "requestId": "51000000-0000-4000-8000-000000000001",
  "fundVersion": 0,
  "id": "52000000-0000-4000-8000-000000000001",
  "kind": "deposit",
  "amount": 420000,
  "date": "2026-11-06",
  "note": "实际存入提前还贷储蓄"
}
```

修正存入，替代记录沿用原 kind，不能改变成支出：

```json
{
  "requestId": "51000000-0000-4000-8000-000000000002",
  "fundVersion": 1,
  "recordVersion": 1,
  "replacement": {
    "id": "52000000-0000-4000-8000-000000000002",
    "amount": 400000,
    "date": "2026-11-06",
    "note": "核对后修正实际金额"
  }
}
```

建立年度目标。carryInAmount=0 时本期已有存入按实际日期归集，不需要再次写成结转：

```json
{
  "requestId": "51000000-0000-4000-8000-000000000003",
  "fundVersion": 2,
  "id": "53000000-0000-4000-8000-000000000001",
  "startDate": "2026-11-01",
  "targetDate": "2027-10-31",
  "targetAmount": 5000000,
  "carryInAmount": 0
}
```

提前结束目标须明确把 targetDate 调整到实际结束日，不能保留未来截止日直接关闭：

```json
{
  "requestId": "51000000-0000-4000-8000-000000000004",
  "fundVersion": 3,
  "version": 1,
  "startDate": "2026-11-01",
  "targetDate": "2026-11-06",
  "targetAmount": 5000000,
  "carryInAmount": 0,
  "state": "closed"
}
```

## 4. 目标、使用与结转

目标只适用于 `saving`，普通定存可以不建目标；周期费用仍使用原 reserve 配置。半年或年度由明确起止日表达，不推断用户真实贷款安排。起始日不能早于用途启用月，同一项目最多一项活动目标；先关闭旧目标，新一期必须从所有历史目标截止日之后开始，不允许重叠或向旧期之前插入目标。

创建目标会按实际日期追认窗口内已有有效存入；新增、修正及补记存入也按日期归入对应目标期，包括已关闭的历史期。期初不自动归入目标，由 `carryInAmount` 明确分配已有储备；窗口内的存入不能既计为本期存入，又计为结转。

结转校验同时受期初／开始前净储备与当前可分配实际资金约束，排除本期窗口存入；修改已有目标时纳入该期已用金额，避免已还贷后不允许保留原筹集历史。结转只是标记资金用途，不创建存入或增加项目余额。不能把未来尚未存入的钱先作为期初结转。

目标响应区分：

| 字段 | 含义 |
| --- | --- |
| collectedAmount | 该期 carryInAmount + 该期有效实际存入，保留筹集历史 |
| usedAmount | 显式归属该目标期的有效 use；支付后仍算已用于目标 |
| availableAmount | 本期筹集减已用，并受项目实际资金约束；可为负 |
| forecast.fundedAmount | 本期可用 + 已用于目标，用于可靠测算缺口 |
| forecast.gapAmount / surplusAmount | 相对于目标额的实际缺口／超出额 |

旧期使用及余款的历史展示和新期结转可引用同一笔余款，因此不能把不同目标期的 availableAmount 相加作为项目余额。项目余额始终以 `balance.availableAmount` 为准。

调整目标日期不能把已有归属存入移出窗口，也不能使关联 use 早于开始日；使用允许晚于目标截止日。关闭不等于已付款或完成，也不会清空钱；关闭后的目标定义不再修改或重新打开，其测算不提供后续筹集月份。实际执行仍可追溯修正，按有效记录重算历史，不能为保留完成标记而冻结错误金额。关闭期的项目可用上限按关闭自然日的有效资金计算，下一期新存款不加入旧期筹集额。

核心算例：实际筹集 50,400 元，既有真实用款 50,000 元 → 项目可用 400 元，目标缺口 0；关闭后下一期分配 400 元结转，项目余额仍为 400 元。再实际存入 4,200 元 → 项目余额 4,600 元；旧目标的筹集历史不增加，新期按 400+4,200 展示。

本步数据库测试通过真实 LedgerEntry 外键显式准备既有 use 来验证上述聚合；该历史结果只证明资金聚合。第 06 步随后通过公共关联 API 完成真实还贷／缴费、目标续建及原账单修改／删除验证，见[账单执行说明](family-budget-execution.md)；use 从原账单生成，不再需要测试准备来代替公共关联。

## 5. 验证结果与复现

- 定向离线：`pytest tests/test_budget_fund_contract.py tests/test_budget_api.py tests/test_budget_contract.py tests/test_budget_math.py tests/test_budget_month_contract.py -q`，**138 passed**。
- 本机 PostgreSQL 17 新增资金／目标真实 API 测试：`pytest tests/test_budget_funds_integration.py -q --tb=short`，**7 passed**。
- 资金、月份和方案联合数据库回归：`pytest tests/test_budget_funds_integration.py tests/test_budget_months_integration.py tests/test_budget_plan_integration.py -q --tb=short`，**20 passed**。

真实测试仅替换鉴权 token 到测试用户的解析，其余请求、SQL、事务、权限与并发使用实际应用和 PostgreSQL。覆盖：资金不随月份增长、历史存入合法快照、归档发现／保留、期初只计一次、可追溯修正与分页、50,400／50,000／400 续建、目标追认与禁止双算结转、闭期真实修正、负余额缺口、成员只读／外部隔离、同请求并发／旧版本冲突、权限转让、故障后整体回滚及原请求重试。

先启动仅绑定本机回环地址、专用数据库名为 `moji_budget_migration_test` 的一次性 PostgreSQL 17 容器，再在 backend 目录执行。连接参数只用于该专用临时容器，不能替换成开发／正式库；测试在随机 schema 内迁移，结束清理 schema。

```powershell
$budgetPreviousFlag = $env:MOJI_RUN_INTEGRATION_TESTS
$budgetPreviousUrl = $env:MOJI_BUDGET_MIGRATION_TEST_URL
try {
    $env:MOJI_RUN_INTEGRATION_TESTS = '1'
    $env:MOJI_BUDGET_MIGRATION_TEST_URL = 'postgresql+asyncpg://budget_test:budget-local-test-only@127.0.0.1:5339/moji_budget_migration_test'
    .\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_budget_funds_integration.py tests/test_budget_months_integration.py tests/test_budget_plan_integration.py -q --tb=short
    if ($LASTEXITCODE -ne 0) { throw '预算数据库回归失败' }
}
finally {
    $env:MOJI_RUN_INTEGRATION_TESTS = $budgetPreviousFlag
    $env:MOJI_BUDGET_MIGRATION_TEST_URL = $budgetPreviousUrl
}
```

统一检查结果与临时环境清理记录见[总计划交接记录](../../docs/家庭预算功能与分步开发计划.md)。默认统一检查跳过显式启用的真实集成测试，上述 20 项已单独实际执行；没有使用 Redis、模型、微信服务或远程数据库。

## 6. 第 06 步交接

第 06 步已完成[账单唯一关联、受控 use、取消关联、原账单编辑／删除和月度执行汇总](family-budget-execution.md)。沿用账本锁、当前成员权限、稳定用途和资金版本；关联时同时校验 entryVersion 与 linkVersion，涉及不同项目按 UUID 顺序锁定。use 仍不能通过本步 opening/deposit 修正接口改写。

目标定义关闭后固定，但有效账单正常修正继续同步派生 use。第 06 步已检验迟到用款、改日期／金额、改为收入、跨合法个人账本移动、删除和取消，防止旧接口留下孤立资金扣减；新月快照复用内部方法，不提前提交。下一步第 07 步接入前端状态及体验数据，保留独立资金和关联版本，按预算通知刷新真实执行。
