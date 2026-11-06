# 家庭预算：数据模型与增量迁移

2026-11-06，第 02 步完成。ORM 位于 `app/models/budget.py`，增量迁移为 `alembic/versions/20261106_0006_create_family_budget.py`，本地单一 head 从 `0005_admin` 变为 `0006_budget`。

已在本机临时 PostgreSQL 17 容器中执行真实升级、约束检查、回退及再次升级。第 03—06 步已接入[方案管理](family-budget-plan.md)、[月度预算](family-budget-months.md)、[资金与目标](family-budget-funds.md)及[账单执行](family-budget-execution.md)；开发库和正式库未执行本次迁移，前端运行时与页面尚未接入。

需求见[总计划](../../docs/家庭预算功能与分步开发计划.md)，字段与事务边界见[正式契约](family-budget-contract.md)。

## 1. 存储结构

所有预算金额、方案／月份／资金版本采用 BigInteger。单笔输入额度与第 01 步一致，范围为 0 或 1 至 999,999,999 分；版本限制在 JavaScript 安全整数内。分配总额和可用资金通过后续服务计算，不在项目表额外保存一个容易与流水不一致的余额。

| 表与 ORM | 主要内容 | 数据库保障 |
| --- | --- | --- |
| `budget_plans` / `BudgetPlan` | 账本方案根对象、全局 version、时间戳 | book_id 唯一，引用原 books；version 从 0 开始 |
| `budget_projects` / `BudgetProject` | 稳定用途、账本、kind、status、enabled_month、fund_version | 项目属于已建方案的账本；性质和状态枚举；启用月份必须为合法月首；fund_version 从 0 开始 |
| `budget_revisions` / `BudgetRevision` | 根方案及账本、月首生效日期、plan JSONB、superseded_at、创建人 | 同方案／生效月份仅一个未被取代版本；复合外键保证方案属于同账本；保留被取代历史 |
| `budget_months` / `BudgetMonth` | 基础方案、override、解析结果 JSONB、来源版本、创建与修改人、version | 账本月份唯一；来源 revision 同账本；月份合法；快照版本从 1 开始 |
| `budget_goal_periods` / `BudgetGoalPeriod` | 项目及账本、起止日、目标额、carry_in_amount、state、closed_at、version | 目标日期有序且在范围内；金额范围；目标与项目同账本；关闭状态与关闭时间一致 |
| `budget_fund_records` / `BudgetFundRecord` | 期初、存入、用款、实际日期、原账单、目标期、修正来源、作废与版本 | 唯一有效期初；每笔账单至多一个有效用款；金额为正；use 必须有账单，其他类型不能有账单；项目／目标／修正来源同用途同账本 |
| `budget_entry_links` / `BudgetEntryLink` | 原账单、项目、目标期、is_deleted、版本与操作者 | entry_id 主键；取消后保留行，不通过重新插入重置版本；目标必须属于相同项目及账本 |
| `budget_mutations` / `BudgetMutation` | 账本及 request_id、actor、operation、请求摘要、成功响应及状态码 | 账本／request_id 联合主键；摘要为 64 位小写十六进制；仅保存成功响应 |
| `budget_previews` / `BudgetPreview` | 随机 token、账本及 actor、操作、请求摘要、依赖版本、影响范围、过期与消费时间 | token 主键且不少于 32 字符；默认有效期 10 分钟；预览可在首次建立方案前创建 |

方案名称、分组和项目配置保存在 revision／月快照中。稳定项目表不以当前名称重写历史，也不每月重新建立项目。分组只承担版本 JSON 内的组织关系，不新建独立余额实体。

`budget_months.override` 默认值通过 `jsonb_build_object` 构造，返回 `{"plannedIncome": null, "itemAmounts": []}`；不把带 `:null` 的 JSON 字符串作为 SQLAlchemy `text` 默认表达式，避免被识别为绑定参数。

## 2. 约束与索引

关键部分唯一索引：

- `uq_budget_revisions_current_month`：`(plan_id, effective_month)`，条件 `superseded_at IS NULL`。替换方案时先标记旧 revision，再创建新 revision；已有快照继续引用原 revision。
- `uq_budget_funds_active_opening`：`project_id`，条件 `kind='opening' AND voided_at IS NULL`。
- `uq_budget_funds_active_use`：`entry_id`，条件 `kind='use' AND voided_at IS NULL`。

资金修正有 `replaces_id` 同项目同账本复合外键，且一个原记录只能有一个直接替代记录；连续修正应引用上一条有效替代记录。原记录作废后可建替代期初或用款，不能同时保留两个有效记录。

月份、项目状态、目标日期、资金日期和目标归集均有查询索引。额外的 `(id, book_id)`、`(id, project_id, book_id)` 唯一键用于复合外键，防止仅凭 UUID 混用另一个账本或另一个用途。

关联原表只引用 `books.id / users.id / entries.id`；迁移不改原表列、索引、数据或 `Book.budget`。没有增加删除级联，来源被快照或资金引用时不能物理删除。

## 3. 服务层校验职责

数据库约束和服务校验职责分开；第 03—06 步的方案、月份、资金和账单同步规则已实现：

- 第 03 步负责完整 JSON 的 Pydantic 校验、稳定项目 kind／账本不可变、方案 JSON 不原地改写、生效版本和影响预览校验。当前 JSONB 的 CHECK 保障对象类型，内部叶子、金额和规模通过既有 schema 校验。
- 第 04 步负责单月覆盖的来源与版本、确认幂等，以及不回写历史和启用前月份。
- 第 05 步在账本锁内检查最多一个活动目标、目标期按历史顺序且不重叠、续期余款、执行日期不晚于业务今天／不早于启用及期初、关闭定义和可追溯修正。实际存入按日期归入目标期，追认归属会递增记录版本；关闭定义固定但实际记录仍允许修正。没有引入需要额外权限的 PostgreSQL 扩展或目标排斥约束。
- 第 06 步已负责原账单属于当前账本、为有效支出且允许当前成员编辑，以及 use 的金额／日期与账单一致。旧客户端编辑、删除、改为收入和合法个人账本转移同事务同步，不用单独任务补偿。取消后关联墓碑继续保留版本，作废 use 的替代链可追溯。
- 原账单外键只引用 entry_id，不增加 entries 的复合唯一键或不可更新关系。这样保留未来跨个人账本修改的能力；关联失效和历史 use 的处理仍由同一事务维护，不用强制账本外键阻断正常修正。
- 每次请求都重新鉴权并按既定账本锁顺序执行；数据库中的 actor 字段是审计归属，不自动授予权限。预览消费及幂等重放也必须重新验证访问资格。

## 4. Alembic 接入与回退

`app/models/__init__.py` 已导入 9 个模型，Alembic 可发现。新迁移冻结显式建表代码，不在执行时引用应用 ORM，也不修改已经发布的 `0001`—`0005`。

`alembic/env.py` 新增可选的 `config.attributes['connection']` 分支：测试可传入自己的同步连接和外层事务，完整运行 Alembic 命令，不另读数据库连接配置。原命令行在线迁移和离线 SQL 入口保持原逻辑。

验证链路：

1. 在随机 schema 中执行空库 → `0005_admin`。
2. 写入独立的测试用户、旧账本和旧账单作为基线。
3. 执行 `0005_admin` → `0006_budget`，核对旧数据、模型与实际库结构，并尝试合法及非法预算写入。
4. 回退至 `0005_admin`，确认只移除预算表，旧数据保留。
5. 再次升级至 `0006_budget`，重新比较结构，确认预算表为空。
6. 回滚外层事务，校验临时 schema 已消失；停止专用临时容器。

回退会移除预算表及其记录，不能用于保留已投入使用的预算数据。正式发布时需要结合备份及服务兼容评估；本次只在一次性测试数据中验证了回退，不对已有开发或正式数据执行回退。

## 5. 验证入口与结果

离线检查（backend 目录）：

```powershell
.\.venv\Scripts\python.exe -m alembic heads
.\.venv\Scripts\python.exe -m pytest tests/test_budget_models.py tests/test_migrations.py -q
```

结果：head 为 `0006_budget`；定向检查 **5 passed**。包含 4 个新增模型／迁移检查与原有迁移模板检查；SQL 输出仅涉及新增预算对象。

真实 PostgreSQL 测试文件为 `tests/test_budget_migrations_integration.py`，必须同时显式开启集成测试并提供专用 URL。它只接受 `postgresql+asyncpg`、本机地址及数据库名 `moji_budget_migration_test`，拒绝开发库和正式库，不访问 Redis 或模型。

下面是可复用的本机 Docker 验证命令，执行位置为 backend；密码只用于本次一次性本机测试容器，不写入 `.env`：

```powershell
$budgetTestContainer = 'moji-budget-migrations-' + [guid]::NewGuid().ToString('N').Substring(0, 12)
$budgetPreviousFlag = $env:MOJI_RUN_INTEGRATION_TESTS
$budgetPreviousUrl = $env:MOJI_BUDGET_MIGRATION_TEST_URL
try {
    docker run --detach --rm --name $budgetTestContainer --publish '127.0.0.1::5432' `
        --env 'POSTGRES_DB=moji_budget_migration_test' --env 'POSTGRES_USER=budget_test' `
        --env 'POSTGRES_PASSWORD=budget-local-test-only' `
        --tmpfs '/var/lib/postgresql/data:rw' postgres:17-alpine
    if ($LASTEXITCODE -ne 0) { throw '测试数据库容器启动失败' }
    $budgetReady = $false
    for ($budgetAttempt = 0; $budgetAttempt -lt 30; $budgetAttempt++) {
        docker exec $budgetTestContainer pg_isready -U budget_test -d moji_budget_migration_test 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $budgetReady = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $budgetReady) { throw '测试数据库未就绪' }
    $budgetAddress = docker port $budgetTestContainer 5432/tcp
    if ($LASTEXITCODE -ne 0) { throw '读取测试数据库端口失败' }
    $budgetPort = ($budgetAddress.Trim() -split ':')[-1]
    $env:MOJI_RUN_INTEGRATION_TESTS = '1'
    $env:MOJI_BUDGET_MIGRATION_TEST_URL = "postgresql+asyncpg://budget_test:budget-local-test-only@127.0.0.1:$budgetPort/moji_budget_migration_test"
    .\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_budget_migrations_integration.py -q
    if ($LASTEXITCODE -ne 0) { throw '预算迁移验证失败' }
}
finally {
    $env:MOJI_RUN_INTEGRATION_TESTS = $budgetPreviousFlag
    $env:MOJI_BUDGET_MIGRATION_TEST_URL = $budgetPreviousUrl
    docker stop $budgetTestContainer | Out-Null
}
```

本次结果：真实 PostgreSQL 17 检查 **1 passed**。验证了部分唯一索引、复合外键、金额／月份／日期／版本、JSON 默认覆盖、幂等键、期初与用款作废替代、旧版本快照保留、预览有效期，以及升级／回退／再次升级。预算表的 Alembic 结构与 ORM 比较无差异，CHECK 名称一致。

首轮真实迁移发现 JSON 文本默认值中的 `:null` 被 SQLAlchemy 解释为参数，已改为 `jsonb_build_object` 并增加默认值实际读取断言；修正后完整真实链路通过。离线 SQL 通过不等同于数据库已执行，这项验证后续继续保留。

根目录 `.\scripts\check.ps1` 已通过：前端全部检查、管理端 TypeScript、后端 **581 passed、15 skipped**、Ruff 和 `pip check`。其中新迁移真实测试默认跳过，已通过上述单独启用的测试实际验收。未运行发行构建、真实开发服务或设备验收。

## 6. 下一步交接

第 03—06 步的[方案接口](family-budget-plan.md)、[月度预算](family-budget-months.md)、[资金／目标](family-budget-funds.md)及[账单执行](family-budget-execution.md)已完成，复用本步存储，未增加迁移。原账单修改／删除在同一事务更新关联与派生资金，不用额外支出代替原账单。下一步第 07 步接入前端服务、状态及体验数据；以下存储交接约束继续适用。

特别注意：首次方案预览引用 books 即可，无需先创建 budget_plans；实际保存时再在账本事务里建立方案根。已确认月份引用旧 revision 不得改绑新版本；替换同生效月 revision 应在一个事务内先 supersede 再插入。后续若需新增字段，创建新的迁移，不回写已完成的迁移文件。
