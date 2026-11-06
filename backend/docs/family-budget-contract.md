# 家庭预算第一版：契约与纯计算

2026-11-06，第 01 步交付。可执行契约位于 `app/schemas/budget.py`，纯计算位于 `app/services/budget_math.py`，前端对应类型位于 `frontend/common/types/budget.ts`。

第 01—06 步已完成输入结构、纯计算、[数据模型与迁移](family-budget-storage.md)、[方案接口](family-budget-plan.md)、[月份与精确均摊接口](family-budget-months.md)、[资金与目标执行](family-budget-funds.md)及[账单关联和执行汇总](family-budget-execution.md)，均在本机临时 PostgreSQL 验证；开发库与正式库尚未升级或发布。第 07 步已接入[前端服务、状态与体验数据](../../frontend/docs/family-budget-state.md)，页面继续按后续步骤实施。

总计划见[家庭预算功能与分步开发计划](../../docs/家庭预算功能与分步开发计划.md)。完整可执行输入见[家庭预算示例 JSON](examples/family-budget-plan.json)，仅用于验证，不自动写入真实账号。示例缴费日期是测试日期，不代表用户已确认的实际缴费安排。

## 1. 核对结果与兼容边界

- `Book.budget` 是原有的单一消费预算；`LedgerEntry` 只有 `expense`、`income`，账户为文本，没有银行余额或真实转账。
- 现有首页、我的、统计与日历沿用账单。预算新建、预留、存入均不生成账单，15,600 总分配也不写入 `Book.budget`。
- `LedgerService.require_book` 使用账本行锁，并在等待后重新核对成员；`require_entry` 跨账本操作按账本 UUID 排序锁定。新服务复用这些顺序，禁止先锁预算／资金行再反向等待账本。
- 第 01 步本地 `alembic heads` 为 `0005_admin`；第 02 步新增 `0006_budget` 后成为单一 head，未升级真实开发库或正式库。
- 原有 `PUT /api/v1/books/{book_id}/budget`、账单 schema 及 `/ledger` 响应不变。新旧预算互不自动覆盖；家庭预算未设置时仍可用旧预算。
- 第 02 步增量迁移接在 `0005_admin` 后，不修改 `0001`—`0005`，未增加应用配置、密钥或依赖；独立数据库测试通过专用环境变量显式启用。

## 2. 字段与数值约定

所有 JSON 对象使用 camelCase，Python 内部使用 snake_case；复用 `LedgerSchema` 的额外字段拒绝规则。UUID 只负责定位对象，不能作为权限凭证。请求路径绑定账本，客户端不能提交 `ownerId`、`userId`、`createdBy` 或可信汇总。

| 项目 | 固定规则 |
| --- | --- |
| 金额输入 | 整数分，单个计划收入、项目月额、预计费用及目标上限为 `999999999` 分，与现有金额上限一致 |
| 零金额 | 计划收入和项目月额允许 0；预计缴费金额和目标金额必须大于 0；目标为空表示不建目标期，不用 0 伪装目标 |
| 类型精度 | 金额、版本和周期拒绝布尔值、浮点数和数字字符串，不自动转换 |
| 派生金额 | 总分配可大于单项上限；以整数计算，JSON 整数绝对值不得超过 `2**53 - 1`，数据库金额采用 BigInteger |
| 负值 | 单项额度不允许负数；待分配和实际可用储备可为负。负储备代表缺口，不自动补造存入 |
| 日期 | `2000-01-01` 至 `2100-12-31` 的 `YYYY-MM-DD`，不接受时间戳、datetime 或日期时间字符串 |
| 月份 | `2000-01` 至 `2100-12` 的 `YYYY-MM`，月份闭区间包含首尾 |
| 业务今天 | 服务端按 `Asia/Shanghai` 提供；纯函数必须显式传入 `as_of`，不读取系统时钟 |
| 名称与备注 | 名称去掉首尾空白，1—24 字符；备注最多 80 字符 |
| 规模 | 每份方案最多 20 个分组、200 个项目；空方案允许保存，用于逐步设置 |
| 版本 | 非负安全整数，0 表示尚未创建；已有快照编辑必须使用正整数版本 |
| 时间戳 | 响应 `generatedAt` 为毫秒，表示实际生成时间；不是计划生效月份 |

总分配只对叶子项目相加。分组不接收金额或子分组；重复分组 ID、重复项目 ID 和指向不存在分组的项目一律拒绝。名称允许相同，不靠名称匹配身份或资金。

`unallocated = plannedIncome - totalAllocated`，允许负值保存。实际收入不会改写 `plannedIncome`。

## 3. 项目、周期与目标

`BudgetPlan = {plannedIncome, groups, items}`；`BudgetGroup = {id, name}`。

`BudgetItem`：

| 字段 | 含义 |
| --- | --- |
| `id` | 账本内稳定项目 UUID；换方案、改名或换组后保持不变 |
| `groupId` / `name` | 所属分组和名称；在方案版本／月份快照里保存当时内容 |
| `kind` | `expense` 当月支出额度、`reserve` 周期费用预留、`saving` 储蓄 |
| `monthlyAmount` | 每月分配金额；无论是否实际存入，都只是计划 |
| `dueDay` | 仅 `expense` 可填写，1—31，否则 null |
| `reserve` | 仅 `reserve` 必填，结构见下；其他性质必须为 null |
| `note` | 备注，默认空字符串 |

稳定项目一旦建立，不允许修改 `kind`；需要改变性质时建立新项目，保留旧项目的余额和历史。分组只在方案 JSON 中保存，稳定项目归属账本且不能被另一个账本复用。

`ReserveCycle = {expectedAmount, anchorDate, cycleMonths}`：

- `cycleMonths` 为严格整数 `1 / 6 / 12`，与项目性质分别保存。
- `anchorDate` 是原始约定缴费日期。第 N 期从原始锚点增加 N 个周期，目标月份没有原约定日时取月末。
- 例：2026-01-31 每月缴费 → 02-28 → 03-31；2024-02-29 每年缴费 → 2025-02-28 → 2028-02-29，不能从上次截短后的日期继续推算。
- 锚点可以早于查询月，服务按期序号展示下一次缴费；不得据此生成早于预算启用日的历史预算或付款记录。
- `expectedAmount` 是一次缴费预计金额，不是已经发生的支出。默认月额由用户确认，允许年度 610,000 分但月额 52,000 分。

储蓄目标单独保存为目标期，不放进每份方案重复创建。没有目标期的 `saving` 项目仍可每月存钱；有目标时关联稳定项目，保存 `id / itemId / startDate / targetDate / targetAmount / carryInAmount / state / version`。

- 日期包含首尾，开始不得晚于截止；不强制必须正好半年或一年，可设置明确的起止自然日。
- 同一项目最多一个活动目标，目标期不重叠；`state=active/closed` 由明确操作改变。日期到期、金额攒够、实际使用分别展示，不自动等同为「已还款」。
- 目标可手动提前关闭，须明确把截止日调整到不晚于业务今天的实际结束日；下一期开始日期在全部历史目标截止后。修改和关闭均记录版本，已关闭目标定义不能修改或重开；真实执行仍可追溯修正，不被后续计划覆盖。
- `carryInAmount` 是已有可用资金分配给新一期的份额，不能超出续建时真实可用正余额；它不新增资金流水，也不再次增加项目余额。
- 开始使用前已有储备通过项目的唯一期初记录登记；不能把期初资金同时作为新存入重复登记。

## 4. 方案版本、单月覆盖与快照

### 4.1 生效版本

每账本一份方案根对象，持有全局 `version`，未设置为 0。发布方案版本时生成不可变 `BudgetRevision = {id, effectiveMonth, plan}`，根版本加一。

纯函数 `effective_revision(revisions, month)` 的输入为当前有效、未被取代的版本集合：选择 `effectiveMonth <= month` 的最大值；之前无版本返回 null。生效月份不能重复，传入版本 ID 也不能重复。

- 「从某月起」新增或替换该月的版本，只作用到下一份已安排的生效版本之前。
- 同生效月份替换时创建新 revision ID，旧 revision 标记被取代并保留；已有快照仍可引用旧 ID。
- 用户只能从业务当前月或未来月份发布，不能回写过去的生效版本。
- 旧版本不能原地修改 JSON。历史还原依靠当时有效的不可变版本，而不是今天的方案。

### 4.2 月份状态

`BudgetMonthResponse` 固定包含 `schemaVersion=1 / bookId / month / state / planVersion / snapshotId / version / sourceRevisionId / plan / override / totals / generatedAt`。

| state | 内容与语义 |
| --- | --- |
| `not_set` | 该月无有效方案；plan、totals、来源 ID、快照 ID 均为 null，version=0，override 为空 |
| `preview` | 按该月有效版本计算；有 plan、totals、来源 ID，快照 ID 为 null，version=0，override 为空；不意味着保存成功 |
| `confirmed` | 保存了一份唯一月快照；有快照 ID、正整数 version、来源 ID、计划及汇总；计划已应用该月覆盖 |

`planVersion` 是当前方案根版本，用于阻止用旧预览确认；`version` 是月快照自己的版本。纯查询不创建快照，不需要后台定时任务。确认或首次记录该月执行时，在账本锁内固化快照。

已确认月份保留快照，不因后续方案修改而刷新。调整后续方案的影响预览须列出这些月份，用户如需改变某份已确认计划，通过单月编辑明确操作。首次执行可固化过去已启用月份的原计划，但不能补造启用前预算或改历史额度。

### 4.3 单月覆盖

`BudgetMonthOverride = {plannedIncome: number | null, itemAmounts: [{itemId, amount}]}`。

- `plannedIncome=null` 表示继承，0 是有效覆盖；`itemAmounts` 只能引用快照基础方案中的叶子项目，项目不能重复。
- 覆盖对象采用完整替换语义；清空列表或将收入设为 null 恢复继承。始终应用到快照保存的基础方案，不能在已覆盖结果上反复累加。
- 纯函数 `apply_month_override` 返回独立副本，不修改原方案，未知项目拒绝处理。
- 单月编辑第一版仅改收入和既有项目额度。修改名称、性质、分组或新增项目走方案版本设置，不在单月输入中偷偷改变结构。
- 月快照保存基础方案、覆盖和解析结果；历史计划与执行分离，实际账单变化仍重新计算执行情况。
- 过去月份不开放预算编辑；当月及未来已确认月使用快照版本更新，冲突返回 409。

### 4.4 影响预览和精确均摊

方案保存前先请求服务端影响预览，返回 `impactToken / expiresAt / version / effectiveMonth / untilMonth / confirmedMonths / overriddenMonths / totals`。`untilMonth=null` 表示没有下一份已安排版本；confirmedMonths 包含所有会保留原快照的已确认月份，overriddenMonths 是其中有单月覆盖的月份。

`impactToken` 是短期服务器预览记录的随机标识，10 分钟有效，绑定操作者、账本、规范化请求内容、方案根版本及受影响快照版本。保存时重新校验；过期、内容或依赖版本变化均返回 409，不依赖新增签名密钥。

`allocate_cents(totalAmount, startMonth, endMonth)` 返回精确月份表：整数商作为基础月额，余数从最早月份起各加 1 分。610,000 分分 12 月 → 前 4 月 50,834 分、后 8 月 50,833 分。固定每月 52,000 的方案保持原值，不被自动均摊覆盖。

精确均摊是显式操作：先预览，再按月份表批量保存对应项目的单月覆盖（第 04 步已实现），不改方案的默认月额。批量接口单次最多 120 个月；只改所选项目，保留其他覆盖，不允许覆盖过去月份。批量操作绑定影响预览和月版本，在同一账本事务内确认必要快照并保存全部覆盖，失败整体回滚。窗口外继续沿用原方案，不自动推断下一年的新分配表。

## 5. 纯计算输出与目标缺口

`summarize_budget` 返回：

| 字段 | 示例值（分） |
| --- | ---: |
| plannedIncome | 1,560,000 |
| expenseAmount | 888,000 |
| reserveAmount | 52,000 |
| savingAmount | 620,000 |
| totalAllocated | 1,560,000 |
| unallocated | 0 |

`groupTotals` 按方案分组顺序返回：固定缴费 484,000、年度预留 52,000、储蓄 620,000、生活费 404,000。888,000 是当月用途支出额度，包含「生活费转账」这一用途名称，不代表已经发生消费。

`BudgetTarget = {startDate, targetDate, targetAmount, availableAmount, usedAmount}` 是测算值对象，不是可直接写储蓄的请求。availableAmount 是归属本期的实际可用资金，usedAmount 是本期实际已用于目标的金额，均由服务端读取执行记录构造，默认 0。持久化目标请求不能包含客户端计算的可用余额或已用金额。

`target_funding_preview(target, as_of=服务端业务今天)`：

- `fundedAmount=availableAmount+usedAmount`；`gapAmount=max(targetAmount-fundedAmount, 0)`；`surplusAmount=max(fundedAmount-targetAmount, 0)`。已用于本期目标的金额仍算已筹集，不因实际使用而需要重新攒同一笔钱。
- 尚可筹集月份从 `max(startDate, as_of)` 所在月起，包含截止月，不按剩余天数折算半个月。
- 截止当日仍可安排；业务今天晚于截止自然日时 remainingMonths=0、additionalSchedule=[]，仍有缺口才为 `overdue=true`，已筹足的不标为逾期；不能除以零或伪造后续月份。
- `additionalSchedule` 是基于本期实际筹集额还需要补充的建议，不是原定月计划，也不是已存入。当前月已存的金额已反映在资金中，不能再把整月计划当作实际加一次。
- 查询日早于目标开始时，从开始月份安排；缺口为零时保留剩余月份但每月建议为 0，余款不被自动清空。
- 负可用余额会增加缺口；超出 JavaScript 安全整数的派生结果拒绝计算。

例：一年目标 5,000,000 分，截至 7 月 15 日真实可用 2,000,000 分，剩 6 个筹集月，缺口 3,000,000 分，建议每月再筹集 500,000 分。月计划 420,000 × 12 = 5,040,000 只是独立计划；实际储蓄仍需登记。

实际提前还贷 5,000,000 分后，本期可用 40,000、已用 5,000,000，则已筹集仍为 5,040,000、缺口 0、余款 40,000。查询旧目标期必须读取该期归属和关闭时记录，不能把下一期新存款加到旧目标。

## 6. 资金、使用与账单关联契约

第 05—06 步已实现期初、存入、修正、目标、公共账单关联及受控 use 同步。实际用款必须关联已有有效支出，不提供客户端任意提交金额的直接扣款接口；原账单编辑／删除入口在同一事务维护关联及派生资金。

资金记录字段：`id / itemId / kind / amount / date / note / entryId / goalPeriodId / version / voidedAt / createdBy / updatedBy`。`kind=opening/deposit/use`；实际金额为正整数分，初始 0 不必创建期初记录。执行日期不得晚于业务今天，也不能早于项目启用和有效期初日期。

- 项目仅一条有效期初记录，金额只增加一次；分次 deposit 可有多条，use 必须引用真实有效支出账单。
- 项目可用金额 = 期初 + 有效 deposit − 有效 use。按查询日过滤记录，修正以作废原记录及登记替代记录保留轨迹。
- 目标期的本期已筹集 = 该期分配的期初余款 + 该期有效存入；已用于目标单独按关联该目标期的 use 汇总。使用资金不会把已筹集历史归零。
- 同一项目不重叠目标期；存入记录按实际日期归入对应目标期；use 显式绑定目标期，可以在目标截止后完成还款，但不能早于该期开始且不能关联别的项目。
- 创建／调整目标追认已有存入并更新记录版本，后续补记／修正也按日期归集，包含已关闭期；不能把本期窗口内存入再计作 carryInAmount。结转受实际可分配资金约束，只分配用途，不产生新现金。
- 目标 availableAmount 受实际项目资金约束；已关闭期按关闭自然日限制资金上限，不纳入下一期的新存款。关闭后目标定义固定，实际记录修正仍重算事实；不同目标期的历史余款不能相加当作项目余额。关闭期 remainingMonths=0、additionalSchedule=[]。
- 实际付款即使超储备仍可记录，项目可用为负并展示缺口。预算额度、储备余额都不限制真实记账。

账单关联字段：`entryId / itemId / goalPeriodId / version / isDeleted / createdBy / updatedBy`。每笔账单至多一条当前关联，取消后保留版本和防重放标记，不因删除后重建丢失版本。

- `expense` 项目按账单发生月归集，预算月份必须已启用该项目；`reserve/saving` 按稳定资金项目及可选目标期归集。
- use 的金额、日期和 entryId 从真实账单派生，客户端不重复提交另一份支付金额。目标还贷用款关联 goalPeriodId；普通无目标储蓄用款可为 null。
- 关联资金项目就产生一次派生 use；改金额、改日期、换项目、取消或删除账单时在同一事务内同步使用记录，不能只刷新统计遗漏余额。
- 收入、删除记录、跨账本记录不可关联；一笔支出不做拆分归集。生活费用途名称或成员姓名不会触发自动关联。
- 编辑账单为收入、转移至另一合法个人账本时，原关联及 use 自动失效并记录活动；共同账单跨账本仍按原规则拒绝。日期移出某个目标期时，清除已不合法的目标期归属但保留有效项目 use，提示重新选择目标期；不拒绝正常账单修正。
- 正常日期修正早于用途／有效期初、晚于业务今天或 expense 发生月无该用途时，取消已不合法关联，不阻止原记账修改。原更新响应以可选 budgetNotice 提示；目标支付晚于截止日仍允许保留明确选择的目标期。
- 退款仍为收入，不自动恢复储备或减少预算消费。支出统计只计算现有账单，不再把 deposit 或 use 作为第二份支出。
- 未关联支出单独汇总；没有账单称「未记录」，不能断言现实未付款。内部交付生活费由用户明确记账口径，预算不制造转账账单。

## 7. 数据实体与一致性要求

第 02 步已按以下责任建表，具体约束、索引、服务校验边界和验证见[存储说明](family-budget-storage.md)：

| 实体 | 关键列与约束 |
| --- | --- |
| `budget_plans` | book_id 唯一、全局 version、created/updated 时间；账本归属不可修改 |
| `budget_projects` | 稳定项目 id、book_id、不可变 kind、status、fund_version；改名和换组的当时内容保存在 revision JSON，不用当前名称重写历史 |
| `budget_revisions` | id、plan_id、effective_month、plan JSONB、superseded_at；同 plan／生效月份仅一个当前有效版本；旧版本和快照引用不得级联删除 |
| `budget_months` | id、book_id、month、source_revision_id、基础方案 JSONB、override JSONB、解析计划 JSONB、version；账本月份唯一 |
| `budget_goal_periods` | id、project_id、起止日、目标额、carry_in_amount、state、version；同项目目标期不重叠，在账本锁内校验 |
| `budget_fund_records` | 资金记录上述字段；唯一有效期初；同 entry_id 最多一条有效 use；作废及替代记录可追溯 |
| `budget_entry_links` | entry_id 唯一、project_id、goal_period_id、version、is_deleted；校验所有关联对象属于同一账本 |
| `budget_mutations` | book_id／request_id 唯一、actor、操作、规范化请求摘要、结果引用或原始写响应；持久化幂等凭据 |
| `budget_previews` | 随机 impactToken、book_id／actor、操作与请求摘要、依赖版本、影响范围、expires_at；过期凭据不能保存 |

名称、分组和完整项目配置保存在版本和快照 JSONB 中；稳定项目承担资金归属，不能每月重建项目。移除项目只影响后续计划，历史快照和资金继续存在；有余额的项目提供归档详情，不抹去资金。

所有写入遵循：鉴权 → 按 UUID 顺序锁所涉账本 → 重新核对有效成员／操作权限 → 检查幂等记录 → 校验版本和业务条件 → 按固定顺序锁项目／月份／关联行 → 更新及活动记录 → 保存幂等结果 → 一次提交。操作中不调用模型或外部支付。

- 同 requestId、同规范化操作及内容重试返回原成功结果；改内容返回 409。权限必须重新检查，不能凭旧幂等记录绕过退群或权限变化。
- 先查幂等，再校验旧版本和预览过期，允许成功响应丢失后重试；事务失败不能留下成功幂等标记。
- 方案编辑使用方案根 version；月度编辑使用月快照 version；资金和目标管理使用项目 fund_version，账单关联同时核对账单 version 与关联 version。资金变化不无谓递增方案版本。
- 月份确认仅创建一次：如已存在，核对预期来源；同来源返回原快照，不同来源返回 409，不能用旧预览覆盖现有快照或其单月覆盖。
- 多笔／跨账本锁按账本 ID、项目 ID、月份顺序统一排序；复用现有跨个人账本编辑顺序。所有账单变更路径，包括旧客户端调用，也必须维护关联一致性。
- 只读响应不暗中持久化，执行聚合按同一事务视图读取；记录生成时间，不跨多个不一致快照拼金额。

## 8. API 边界与示例

下表中第 03—06 步的方案、月份、资金、目标、关联和执行汇总均已实现。前缀 `/api/v1/books/{book_id}/budgets`，需要既有 Bearer 鉴权。无权对象与不存在对象统一 404；成员能看当前有效共同账本预算，创建者管理计划／资金／目标，普通成员可关联自己可编辑的账单。

| 方法和后缀 | 请求 → 响应 | 步骤 |
| --- | --- | --- |
| GET `/plan` | 无请求体 → `{schemaVersion, bookId, version, revisions}`，未设置 version=0、revisions=[] | 03 |
| POST `/plan/preview` | `PreviewBudgetRevision` → 第 4.4 节影响预览 | 03 |
| PUT `/plan` | `SaveBudgetRevision` → `{schemaVersion, bookId, version, revision}` | 03 |
| GET `/months/{month}` | 无请求体 → `BudgetMonthResponse` | 04 |
| POST `/months/{month}/confirm` | `ConfirmBudgetMonth` → `BudgetMonthResponse` | 04 |
| PUT `/months/{month}` | `UpdateBudgetMonth` → `BudgetMonthResponse`，override 完整替换 | 04 |
| POST `/equalizations/preview` | `{version, itemId, totalAmount, startMonth, endMonth}` → `{impactToken, expiresAt, schedule, affectedMonths}`，schedule 由服务端生成 | 04 |
| POST `/equalizations` | 上述参数加 `{requestId, impactToken}` → `{schemaVersion, months: BudgetMonthResponse[]}`，批量事务 | 04 |
| GET `/items?after=UUID&limit=50` | 无请求体 → `{schemaVersion, bookId, items, nextCursor}`，包含归档用途 | 05 |
| GET `/items/{item_id}` | 资金分页 beforeRecordId/recordLimit，账单分页 beforeEntryId/entryLimit；返回原资金详情及 entries/nextEntryCursor | 05—06 |
| POST `/items/{item_id}/funds` | `{requestId, fundVersion, id, kind: opening/deposit, amount, date, note}` → 记录及最新项目资金版本 | 05 |
| POST `/items/{item_id}/funds/{record_id}/correct` | `{requestId, fundVersion, recordVersion, replacement: {id, amount, date, note} \| null}`，null 表示作废 → 修正结果 | 05 |
| POST `/items/{item_id}/goals` | `{requestId, fundVersion, id, startDate, targetDate, targetAmount, carryInAmount}` → 目标期及资金版本；续建复用该端点 | 05 |
| PUT `/items/{item_id}/goals/{goal_id}` | `{requestId, fundVersion, version, startDate, targetDate, targetAmount, carryInAmount, state}` → 目标期及资金版本 | 05 |
| GET `/items/{item_id}/funding-preview?goalId=...` | 只接受 goalId，服务端读资金和日期 → `BudgetFundingPreview`，不能指定 availableAmount／已用金额／日期 | 05 |
| GET `/entries/{entry_id}/link` | 无请求体 → 账单及关联版本、最新关联；未建立为 0，取消后保留 linkVersion | 06 |
| PUT `/entries/{entry_id}/link` | `{requestId, entryVersion, linkVersion, itemId, goalPeriodId}` → BudgetEntryLinkState，关联及受影响项目资金版本 | 06 |
| DELETE `/entries/{entry_id}/link` | JSON 正文 `{requestId, entryVersion, linkVersion}` → BudgetEntryLinkState，保留取消后的关联版本 | 06 |
| GET `/months/{month}/execution` | 无请求体 → `{schemaVersion, bookId, month, generatedAt, budget, expenseTotal, linkedExpenseTotal, unlinkedExpenseTotal, items}`，真实支出只按账单计算一次 | 06 |
| GET `/months/{month}/entries` | itemId 或 unlinkedOnly 可选且互斥，beforeEntryId/limit 分页 → `{schemaVersion, bookId, entries, nextCursor}` | 06 |

项目停用通过新方案省略该项目，保留 `budget_projects` 与归档详情，不提供物理删除接口。资金修正只允许 opening/deposit，派生 use 只能通过关联或原账单修正。目标编辑不能把已有存入／使用移出允许范围或回写已关闭历史；先处理相关执行再改变目标归属，错误返回 409。

项目列表按 UUID 升序，流水按创建时间及 UUID 降序，默认／最大页数为 50／100 和 20／100。流水分页包含作废审计，汇总使用全部有效流水。item.configuration 是最近历史方案配置，不等于当月计划；status 按当前／未来方案及快照动态计算。归档允许补记／修正资金，新目标须恢复用途后创建。请求及响应完整说明见[资金与目标执行](family-budget-funds.md)。

关联账单按日期及 UUID 降序翻页，默认 20、最大 100，游标须属于当前筛选。月度汇总包括已归档但当月有实际执行的用途；availableAmount 为截至今天累计资金，不是历史月份期末。完整响应和原记账 budgetNotice 说明见[账单关联与执行汇总](family-budget-execution.md)。

保存请求示例（生产中需先获得真实 impactToken，plan 为完整叶子方案）：

```json
{
  "requestId": "40000000-0000-4000-8000-000000000001",
  "version": 0,
  "effectiveMonth": "2026-11",
  "impactToken": "server-preview-token",
  "plan": {
    "plannedIncome": 1560000,
    "groups": [{"id": "20000000-0000-4000-8000-000000000003", "name": "储蓄"}],
    "items": [{
      "id": "30000000-0000-4000-8000-000000000006",
      "groupId": "20000000-0000-4000-8000-000000000003",
      "name": "提前还贷",
      "kind": "saving",
      "monthlyAmount": 420000
    }]
  }
}
```

以上只包含一个储蓄项目，剩余 1,140,000 分显示待分配，不会自动填满其他支出。

确认请求：`{requestId, sourceRevisionId, planVersion}`。单月修改请求：`{requestId, version, override: {plannedIncome: null, itemAmounts: [{itemId, amount: 0}]}}`；0 是有效额度，不表示删除。

错误沿用现有 `ServiceError` 和 API 校验响应，不引入未实现的错误协议：

| HTTP | 语义 |
| --- | --- |
| 401 | 登录失效 |
| 403 | 仍为有效成员但没有该操作权限 |
| 404 | 对象不存在、不属于当前账本或当前无权访问 |
| 409 | 版本／来源冲突、预览失效、幂等内容改变、对象状态或执行依赖冲突 |
| 422 | 金额、日期、分组、项目性质、范围或字段不合法；不能原样回显敏感输入 |

计划超收入、实际支出超额度和储备不足属于可保存并展示的业务状态，不应一律作为 422 阻止记录。所有预算响应建议 `Cache-Control: no-store`；返回错误仍沿用现有中文提示原则。

## 9. 本步验证与下一步

离线测试覆盖完整示例、精度与上限、父子防重复、非法输入、单月副本隔离、历史生效版本、跨年、闰年月末、周期锚点、目标中途开始、截止日、超目标、负余额缺口与关联严格版本。前端类型纳入现有 TypeScript 检查；后端方案、月份、资金、目标、关联及执行 API 已注册，前端运行时已按第 07 步接入，UI 尚未接入。

实际命令和验证结果记录于[总计划交接记录](../../docs/家庭预算功能与分步开发计划.md)。统一检查关闭真实数据库／Redis 集成及模型请求。本步不以通过离线测试宣称迁移、并发 SQL、真实接口或设备已经验收。

第 01—06 步已完成，方案、月份、资金及账单执行服务已落实权限、生效版本、项目身份、覆盖、均摊、目标续建、关联同步及幂等；本机 PostgreSQL 预算 API 联合回归 32 passed，包含旧记账修改／删除和锁等待后权限变化。下一步第 07 步接入前端服务、store 与隔离体验数据，不能以类型编译或后端检查代替真实前端交互验收。

第 07 步前端 API、store、隔离体验与 17 项业务检查已完成，详见[前端交接](../../frontend/docs/family-budget-state.md)；当前下一步为第 08 步方案设置页面。
