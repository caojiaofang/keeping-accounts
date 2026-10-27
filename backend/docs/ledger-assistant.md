# 个人云端记账与智能识别

2026-10-24 个人及共享记账已部署至正式版本 `20261024T121036Z` / `0004_shared`，见[上线记录](releases/20261024T121036Z.md)。开发库与正式库结构一致，共享扩展详见[共享账本说明](shared-books.md)。

## 个人账本接口

全部请求使用 `Authorization: Bearer <token>`，用户身份由登录态确定，请求中禁止指定 `ownerId` 或 `createdBy`。响应字段使用 camelCase。

| 方法 | 路径（前缀 `/api/v1`） | 用途 |
| --- | --- | --- |
| GET | `/ledger` | 读取有权访问的个人及共享账本、全部有效账单，首次访问创建空的日常账本 |
| POST | `/books` | `{id, name, kind?, memberName?}` 创建账本，默认个人账本 |
| PUT | `/books/{id}/budget` | `{budget, version}` 修改固定月预算 |
| POST | `/entries` | 创建账单，客户端为每次新增操作生成固定 UUID |
| PUT | `/entries/{id}` | 修改账单，提交原始版本号 `version` |
| DELETE | `/entries/{id}?version=1` | 删除账单，成功返回 204 |
| POST | `/assistant/parse` | `{text, bookId}` 生成一句话记账草稿 |
| POST | `/assistant/record` | 文字、单张图片及待确认上下文的连续记账；已部署至 `20261027T052736Z` |

账单创建示例：

```json
{
  "id": "11111111-2222-4333-8444-555555555555",
  "bookId": "从 /ledger 响应获取的账本 UUID",
  "type": "expense",
  "categoryId": "food",
  "amount": 800,
  "date": "2026-10-24",
  "time": "08:00",
  "account": "微信",
  "note": "早餐"
}
```

金额以整数分传递，单笔范围为 1～999999999 分，预算允许为 0。日期范围为 `2000-01-01`～`2100-12-31`，可选时间为 `HH:MM`，未提供时省略。收支分类与前端内置分类一致，不允许收入使用支出分类。响应新增 `createdAt`（毫秒时间戳）、`createdBy`（可信登录用户 ID）和 `version`。

同一个 UUID 和相同内容的创建请求可安全重试，不会生成重复数据；同一 ID 对应不同内容时返回 409。删除保留服务端标记，重复删除成功，重放已删除的创建请求不会恢复账单。编辑账单与预算使用版本检查，过期版本返回 409；重新读取并核对最新数据后再编辑。其他用户的账本或账单统一返回 404，不泄露其内容。

当前 `/ledger` 返回有权访问的完整账本快照，供明细、统计、日历和 CSV 导出共用，尚无单独的统计或自定义分类接口。共享账本包含其他成员及已退出成员的历史有效账单；成员失效后不再返回该账本。共享权限、修改人字段和接口见[共享账本说明](shared-books.md)。

## 小程序存储与失败处理

- 登录后读取云端；写入成功才更新界面。请求失败不伪装保存成功，也不转成本地记账。跨设备通过进入页面、回到前台及手动刷新读取最新数据，暂不提供推送和离线写入队列。
- 本机缓存按后端地址、用户 ID 隔离，键为 `moji-cloud-v1:<后端地址>:<用户ID>:ledger`。缓存空间不足不影响已成功保存的云端记录。
- 体验区继续使用 `moji-demo-v1`，不会自动上传示例。原账号本机数据 `moji-user-v1:<用户ID>:ledger` 保持原样；如有记录，「我的」提供旧记录 CSV 导出入口，不自动迁移。
- 请求响应迟于账号切换或新写入时，不覆盖新状态。新增表单和助手草稿在页面内重试时复用固定 ID；离开页面前如无法确认写入结果，先刷新明细核对。

## 模型配置与识别

本地 `backend/.env` 已配置用户提供的中转站和密钥；该文件被 Git 忽略。其他环境使用 `.env.example` / `.env.production.example` 的空密钥模板配置，不复制真实密钥到前端、文档或日志。

```dotenv
LLM_BASE_URL=https://api.zf-yun.cn
LLM_API_KEY=<后端专用密钥>
LLM_MODEL=gpt-6-luna
LLM_API_MODE=responses
LLM_TIMEOUT_SECONDS=45
```

**推理强度固定为 `xhigh`**，前端不能修改。Responses 请求发送 `reasoning: {"effort":"xhigh"}`；可选 Chat Completions 适配发送 `reasoning_effort: "xhigh"`。当前已实测的是 Responses 协议及严格 JSON Schema 输出，Chat Completions 仅通过模拟协议检查。

模型收到用户输入、服务端的当前上海日期和分类说明，不发送已有账单、OpenID 或登录 token。模型只生成草稿，服务端再次检查金额、日期、时间与分类；最多 10 笔，缺少金额或日期有歧义时要求补充，未提时间保持空值。未说明账户时明确展示默认「微信」，可修改。确认时走普通账单接口，单笔及批量确认均防止页面内重复保存。真实聊天分析和历史会话已接入，见[聊天消费分析](chat-analysis.md)。目前没有流式识别或持久化草稿。

识别按登录用户限制每分钟 10 次；上游超时返回 504，上游不可用或输出不完整返回 502，限流返回 429。不自动重试付费模型请求，也不把上游错误正文返回小程序。

新客户端的连续记账、图片限制、隐私范围及发布要求见[文字聊天与图片记账](conversational-record.md)。旧 `/parse` 仅接收文字；新 `/record` 可以携带最近对话和当前待确认草稿，仍不直接保存账单，也不持久化记账聊天。

协议格式参考 [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)，中转站兼容性以实测为准。2026-10-24 已使用 `gpt-6-luna` + `xhigh` 实测早餐金额及 08:00、多笔记账、不补造时间、缺失金额要求补充。

## 检查

后端默认 `pytest` 不连接外部服务，覆盖协议、固定推理强度、输入校验和安全错误。真实开发库闭环测试使用临时用户及外层事务，结束后回滚测试数据并清理专用 Redis key，不修改现有用户记录。

```powershell
# 在 backend 目录执行；只访问开发数据库。
$env:MOJI_RUN_INTEGRATION_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest tests/test_ledger_integration.py tests/test_auth_integration.py -q
Remove-Item Env:MOJI_RUN_INTEGRATION_TESTS
```

另设 `$env:MOJI_RUN_LLM_TESTS = '1'` 可让账本集成测试中的识别接口调用真实模型，会产生一次模型请求；未设置时使用模拟模型。测试结束后移除该环境变量。

前端 `npm run check:auth` 同时覆盖云端读写、响应丢失后重试、迟到响应、账号隔离、旧数据保留及确认前不保存草稿；其他本地演示检查继续保留。
