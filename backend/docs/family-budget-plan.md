# 家庭预算：方案与项目管理接口

2026-11-06，第 03 步交付。新增 `app/api/v1/budget.py`、`app/services/budget.py`，补齐 Python／TypeScript 响应类型；已注册方案读取、影响预览、保存三个接口。第 04—06 步已接入[月度预算与精确均摊](family-budget-months.md)、[资金和目标](family-budget-funds.md)及[账单执行](family-budget-execution.md)，下一步为前端服务与状态。

需求及进度见[总计划](../../docs/家庭预算功能与分步开发计划.md)，字段约定见[正式契约](family-budget-contract.md)，数据表与迁移见[存储说明](family-budget-storage.md)。本步未增加迁移，仓库 head 仍为 `0006_budget`。开发库、正式服务及小程序未发布本次功能。

## 1. 可用接口

前缀 `/api/v1/books/{book_id}/budgets`，使用现有 Bearer 登录态。账本取自路径，用户取自登录态，不能由请求体指定。

| 方法与路径 | 请求 | 响应 |
| --- | --- | --- |
| GET `/plan` | 无请求体 | `BudgetPlanResponse`：schemaVersion、bookId、version、revisions |
| POST `/plan/preview` | `PreviewBudgetRevision`：version、effectiveMonth、plan | `BudgetImpactResponse`：impactToken、expiresAt、version、effectiveMonth、untilMonth、confirmedMonths、overriddenMonths、totals |
| PUT `/plan` | `SaveBudgetRevision`：以上预览输入，加 requestId、impactToken | `SavedBudgetRevision`：schemaVersion、bookId、version、revision |

所有成功响应 `Cache-Control: no-store`；业务错误沿用中文 `ServiceError`。SQL 错误回滚后统一为 503，不返回连接、SQL 或数据库原始错误。字段校验错误由现有 API 校验处理器返回 422，不回显原始输入。

读取不存在的预算返回 version=0、revisions=[]，不自动建立方案根或演示数据。读取只返回当前有效且按生效月份升序排列的 revision；被取代版本继续存储，已有月快照仍引用它。

有权访问的个人拥有者和共享成员可读。预览和保存均要求账本创建者；无权账本／项目与不存在统一返回 404，有效普通成员修改共同预算返回 403。

`version` 是方案根的全局版本，第一次保存从 0 到 1；不是原 `Book.version`，也不是独立的月快照版本。空方案、零收入和总分配超过计划收入均允许保存。

## 2. 预览与保存流程

1. GET 读取最新根版本与已安排的方案。
2. 在当前月或未来月份提交完整计划预览，金额、叶子结构与周期由既有 Pydantic 契约校验。
3. 展示总分配、待分配和影响月份。`untilMonth` 为下一份已安排方案生效前的最后一个月，无下一份方案则为 null。
4. 提交相同的规范化预览输入和 impactToken，并为这次保存生成固定 requestId。失败重试保留 requestId 和原请求，不重新替换 token 或正文。
5. 保存返回新 revision 和根版本。下一次编辑重新 GET；幂等重放返回当时的成功响应，不能将它当成其他设备修改后的最新状态。

预览 token 10 分钟有效，绑定当前操作者、账本、操作、规范化请求摘要、根版本、北京时间业务月和影响区间内已确认快照的 ID／月份／版本。

保存时在账本锁内重新计算影响范围。预览过期、被消费、正文变化、其他设备修改根版本、受影响月份新增快照或快照版本改变均返回 409。区间外快照和下一份已明确安排的方案不会被静默改写。

北京时间按项目既有 UTC+8 口径处理，默认时钟为 UTC，进入业务校验时转换；不依赖 Windows 上可能未安装的 tzdata。月末跨北京时间零点后，过去月份不能再发布新方案。

## 3. 项目更新与历史保留

项目作为完整 plan 的叶子管理，不额外增加物理删除接口：

- 新项目用新的 UUID 注册，保存其账本、性质及最早启用月份；plan 维护分组、名称、月额、周期和备注。
- 改名、换组和调整月额保持项目 UUID，已有资金、目标及关联不重新创建。
- 同项目不能改变 kind，返回 409；其他账本已使用该 UUID，返回 404。新项目注册使用冲突保护，即使另一个账本在预览后并发抢先注册，也不会被误收为本账本项目。
- 新增更早但仍合法的生效版本时，enabled_month 只可提前，不抹掉既有启用历史。
- 停用通过在新方案省略项目实现。旧 revision、月快照、资金记录及 fund_version 不删除或重置。
- 同一生效月份替换时，只给旧 revision 记 superseded_at，插入新 revision；不原地修改旧 plan JSON。
- 保存不会改现有已确认月快照，也不改后续已安排版本。下个月的预算是否沿用新方案由下一步的月份服务解析。

项目 status 在方案保存时按当时业务月同步：当前有效方案、所有后续生效方案及当前／未来已确认快照中的项目为 active，其余为 archived。未来停用不应使仍在当月计划内的项目立即消失。该状态是最近方案写入时的汇总，不是银行余额，也不是跨月自动执行；后续月份／资金服务应结合实际查询月解析计划，不能只凭缓存 status 判断当期用途。

储蓄目标期继续按正式契约在第 05 步独立管理，本步不将目标重复塞进每个方案 revision，也不创建存款或支出。

## 4. 事务、重试和权限

保存的关键顺序：

鉴权并锁账本 → 重新核对成员和创建者 → 查同 requestId 的成功记录 → 验根版本及日期 → 验 token 与重新计算的影响范围 → 建根方案和稳定项目 → 取代同月 revision 并插入新版本 → 同步项目状态 → 存成功幂等响应及共同账本活动 → 一次提交。

- 幂等凭据以 `(book_id, request_id)` 定位，额外验证 actor、操作和规范化完整请求摘要。改内容重试返回 409。
- 幂等读取先于旧版本／预览过期校验，使已保存但响应丢失的请求仍可可靠重试；当前权限检查更早执行，退群或转让后不能凭旧成功记录继续写。
- 方案根修改通过原有账本排他锁串行处理；项目 UUID 以确定顺序注册，使用 `ON CONFLICT DO NOTHING` 后重新核对归属，避免两个账本把同一 UUID 认成各自项目。
- 共享账本记录 `budget_plan` 活动，只记新版本、生效月和 revision ID；重试不重复记录活动。
- ServiceError 和 SQLAlchemyError 均在依赖层回滚；失败不留下根方案、部分项目、消费过的 token 或成功幂等标记。
- 读取在账本共享锁下查询根版本和有效 revisions，避免将两个提交时点的结果拼到一起。

## 5. 请求示例

完整家庭示例见[JSON 文件](examples/family-budget-plan.json)。以下只设置提前还贷每月存入额度，其余收入仍为待分配，不自动补造其他项目。

```json
{
  "version": 0,
  "effectiveMonth": "2026-11",
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

POST 预览后，给上面的对象添加 `requestId` 和服务端返回的 `impactToken`，再 PUT 保存。实际使用时选择北京时间当前月或未来月，不能将文档中的示例日期当成永久有效的生效月份。

## 6. 验证与复现

离线定向检查：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_budget_api.py tests/test_budget_contract.py tests/test_budget_math.py -q
```

本次 **101 passed**，包括真实 API 参数绑定、伪造身份拒绝、401、数据库错误脱敏与回滚、北京时间跨月边界，以及原有金额和日期契约。

真实测试位于 `tests/test_budget_plan_integration.py`。复用[存储说明的本机 Docker 启动与清理流程](family-budget-storage.md)，使用同样的本机测试库和环境变量，将 pytest 测试目标换为：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_budget_plan_integration.py -q
```

本次本机 PostgreSQL 17 **5 passed**：

| 场景 | 验证 |
| --- | --- |
| 完整方案回读与未来版本 | 15,600 总分配、4,040 生活费、520 预留、4,200 储蓄；改名换组不换项目；后续方案和月覆盖保留 |
| 预览、过期与重放 | 正文变化、过期、新快照或快照版本变化拒绝；已成功请求在过期／跨月后仍可原样重放 |
| 权限与隔离 | 非成员 404、普通成员只读、其他账本项目拒绝、性质不可变、移出和转让后的权限重核 |
| 并发事务 | 相同请求只保存一次；同旧版本不同请求一成一冲突；不同账本抢同一项目 UUID 一成一拒绝且无孤立根方案 |
| 停用与恢复 | 归档保留资金和旧 JSON，重新纳入方案复用原项目与资金版本 |

这些测试使用真实 FastAPI 路由、Pydantic、服务、SQL 和独立连接事务，仅模拟登录 token 到用户 ID 的解析，不连接 Redis、微信或模型。每例只在本机专用 `moji_budget_migration_test` 中创建随机 schema，执行真实迁移，结束后删除自身 schema；专用容器验证后清理。

统一入口 `.\scripts\check.ps1` 最终通过：前端全部检查、管理端 TypeScript、后端 **586 passed、20 skipped**、Ruff 和 `pip check`。完整交接见[总计划交接记录](../../docs/家庭预算功能与分步开发计划.md)。本步未新增 ORM 或迁移，未升级开发／正式库，未上传小程序或运行设备验收。

## 7. 下一步

第 04 步的[月份、确认、覆盖与均摊](family-budget-months.md)已实现，复用本步的权限、账本锁及共享幂等助手。新增或修改月快照后，依赖版本校验会使旧方案预览失效；既有方案接口数据库回归通过。

第 05 步已提供资金、目标和归档用途发现／详情：从包含已替换版本的历史 revision 恢复最近配置，读取时动态计算当前／未来是否仍安排该用途，保留资金和可追溯记录。第 06 步已扩展账单关联、分页及执行汇总，不以当前有效方案的项目列表替代所有历史用途；下一步第 07 步接入前端运行时。
