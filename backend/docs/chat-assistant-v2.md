# 聊聊花费 V2：接口与模型接入契约

2026-10-27，第 01 步交付。本文固定后续开发的契约；本次已添加可执行 Pydantic 数据结构、协议探测及离线测试，尚未注册 V2 业务路由、执行查账工具或迁移数据库。

分步计划见[智能助手开发计划](../../docs/聊聊花费智能助手开发计划.md)，旧功能见[聊天消费分析](chat-analysis.md)。

2026-10-28 第 02 步已完成独立的[日期与组合筛选](chat-v2-filters.md)，第 03 步已完成[只读查账工具与事实输出](chat-v2-tools.md)，落实下文的条件、查询、分页和卡片约束；模型编排和 V2 业务路由仍按后续步骤实施。

2026-11-04 第 04 步已完成[可修正的对话上下文](chat-v2-context.md)，支持受控编辑、历史来源恢复、有效账单引用、有限历史输入和原子保存。下一步为第 05 步工具循环。

2026-11-04 第 05 步已完成[有边界的多步工具循环](chat-v2-loop.md)，接入原生工具及显式结构化模式，统一次数、超时、输出规模、取消和重复保护。当前返回内部待校验正文与事实，第 06 步再生成可信回答和动态追问；V2 API 与前端仍未启用。

2026-11-04 第 06 步已完成[可信回答与动态追问](chat-v2-answer.md)：使用本轮后端事实生成正文，验证数值/引用，按需选择卡片，生成并校验保存建议；点击建议恢复原范围并重查。准备回答不增加模型请求，沿用原整轮期限。下一步为第 07 步 V2 会话、持久化与 SSE 接入。

2026-11-04 第 07 步已完成[V2 会话、历史及 SSE 接入](chat-v2-api.md)，路由已注册，`CHAT_V2_ENABLED` 默认关闭。消息支持完整/部分保存、幂等重放、锁/限流、断连取消和旧会话只读续聊；下一步为第 08 步前端状态与流处理。

2026-11-05 第 08 步已完成[前端状态与流处理](../../frontend/docs/chat-v2-state.md)，实现能力协商、当前账本历史、权威上下文、预览替换、部分状态、原 ID 重试和显式旧会话续聊。第 09 步已完成[专用结果卡和账单入口](../../frontend/docs/chat-v2-cards.md)的开发和本地验收，微信设备验收待补。服务端开关仍关闭，未部署或上传；第 10 步[真实场景评测](chat-v2-evaluation.md)已完成，原生分批最终覆盖 39/39 表达、兼容冒烟复测 3/3，下一步为第 11 步验收及发布准备。

## 1. 已核对的现有实现

| 位置 | 当前能力及复用方式 |
| --- | --- |
| `app/integrations/llm.py` | Responses / Chat Completions、严格结构化输出、正文流、固定 `xhigh`；第 05 步新增 `chat_v2_llm.py` 复用配置及异常原则，实现 V2 串行工具续接与有界响应读取 |
| `app/services/chat.py` | 消息 ID 幂等、Redis 会话锁、调用限流、短事务保存、取消与权限复核；继续复用业务原则，不让模型绕过权限 |
| `app/services/analysis.py` | 整数分汇总、同期比较、已删除过滤与共享账本访问条件；第 02—03 步扩展查询范围和工具 |
| `app/models/chat.py` | 会话包含 `month/context/version`，消息包含 `result/context` JSONB；`answer` 长度为 2500，`text` 为 200，V2 保持此长度上限 |
| `frontend/store/chat.ts` | 第 08 步已接入 V1/V2 协商、V2 当前账本历史与权威范围、部分结果、可靠重试及 generation 防迟到；旧卡片只读与新建续聊保留 |
| `frontend/common/api/chat-stream.ts` | 跨字节 UTF-8 与 SSE 分片解析、微信分块请求 / H5 fetch；复用解析原则，新增 V2 事件校验 |
| Alembic | 仓库迁移 head 为 `0005_admin`，父版本 `0004_shared`；本次仅运行离线 `alembic heads`，未读取数据库当前版本 |

第 01 步不更改旧接口、前端展示、真实 `.env`、数据库表和已发布环境。

## 2. V2 API 边界与兼容方式

以下路由已在第 07 步实现，使用既有 Bearer 鉴权。默认 `CHAT_V2_ENABLED=false`：能力端点返回实际开关，其他 V2 业务返回 404，不代表已部署或已经启用小程序。

| 方法 | 路径 | 请求和响应 |
| --- | --- | --- |
| GET | `/api/v1/assistant/v2/capabilities` | 已认证后返回 `schemaVersions: [2]` 和实际 `enabled`；不返回密钥、模型地址或内部限额 |
| POST | `/api/v1/assistant/v2/conversations` | `CreateConversationV2` → `ConversationSummaryV2` |
| GET | `/api/v1/assistant/v2/conversations?bookId=<UUID>` | 当前账号、账本最近二十个有回答的 V2 会话，不按入口月份过滤 |
| GET | `/api/v1/assistant/v2/conversations/{id}?before=<sequence>` | `ConversationResponseV2`，默认最近二十轮，按序号向前分页 |
| POST | `/api/v1/assistant/v2/conversations/{id}/messages/stream` | `SendMessageV2`，返回下文 SSE |
| GET | `/api/v1/assistant/v2/legacy/conversations/{id}?before=<sequence>` | 旧会话只读适配，`sourceSchemaVersion=1/readonly=true` |
| POST | `/api/v1/assistant/v2/legacy/conversations/{id}/fork` | `CreateConversationV2`，同账本新建 V2 会话，原记录不改写 |

创建请求为 `{id, bookId, initialMonth}`。`initialMonth` 只初始化讨论范围，历史页展示当前账本全部 V2 会话。消息请求为 `{id, text, suggestionId?}`；复用消息 UUID 和 200 字符上限，首尾去空白。

`ConversationResponseV2` 增加必需的顶层 `context`，始终返回当前权威成功上下文；较早历史页不能覆盖当前范围。消息按序号升序，`before` 为严格小于该序号，默认二十轮；`hasMore` 指示是否还有更早记录。

客户端不能提交查询工具、事实、上下文、用户身份和其他账本 ID。`suggestionId` 只引用服务端已经保存且属于当前会话的建议；后端核对所属轮次、文本和引用权限，不能按客户端传入的正文执行任意动作。

兼容决策：

- 旧路由保持原结构和行为，旧客户端继续使用 V1；V1 不追加 V2 消息。
- V2 会话显式标记版本，V1 历史接口不列出 V2 会话，V2 列表不混入可继续写入的 V1 会话。
- 第 04 步已提供旧会话只读适配及新建续聊服务，第 07 步接入接口入口：先校验权限，将旧 `month/categoryId/entryType` 映射为初始上下文，重新查询后再生成新回答。原会话与统计快照不改写。
- V2 的当前月份默认截到业务今天，已结束月份采用完整月份；未来日期及超长范围的处理在第 02 步统一实现并测试。
- V2 客户端发送前读取能力端点。仅明确收到 V2 未启用或能力端点 404 时，允许保留旧入口；401、403、超时及 5xx 不触发偷偷重发到 V1。消息发送以后不能自动换协议重复调用模型。
- 同一消息 UUID 在重试期间保持不变；文本或 `suggestionId` 不一致返回 409。只有 `done` 确认消息已持久化。
- 第 04 步确定使用已有 `context.schemaVersion=2` JSONB 标记协议，已有 `version` 用于所有已保存轮次的并发控制（含明确部分结果），无需新增版本列或迁移。V1 服务已排除 V2 会话；第 07 步复用现有索引前缀，未修改迁移链。

## 3. 查询筛选与工具参数

可执行定义：`app/schemas/chat_v2.py`。对外 JSON 统一 camelCase，后端代码使用 snake_case。全部对象拒绝额外字段。

`EntryFilter` 字段：

| 字段 | 含义 |
| --- | --- |
| `dateRange: {start, end}` | YYYY-MM-DD 闭区间，支持 2000—2100 年；实际跨度最多为配置天数。未来结束日期截到业务今天并附说明，整段未来拒绝 |
| `entryType` | `expense`、`income` 或 `both`，默认 `expense`；`both` 的结果必须分开列出收支，不将两者相加称为消费 |
| `categoryIds` | 同字段 OR，空数组不限制分类，分类与收支合法性在执行前校验 |
| `keywords: {include, mode, exclude}` | 只匹配备注，`mode=any/all` 控制包含词 OR/AND；任意排除词命中即排除，空包含词不加包含条件 |
| `accounts` | 已保存的账户文本精确匹配，同字段 OR，不推断银行卡余额 |
| `creatorIds` | 共享账本记账人 UUID，同字段 OR；不等同于付款人 |
| `amountMin/amountMax` | 可空整数分，按原始正数金额筛选；`minInclusive/maxInclusive` 默认 `true`，“超过”使用不含等号 |
| `weekdays` | ISO 星期 1—7，空数组为不限，同字段 OR；周末为 `[6,7]` |
| `excludedEntryIds` | 排除已确认引用的账单 UUID，不授予任何额外访问权限 |

不同字段之间使用 AND。第 02 步已实现不区分大小写的字面子串匹配及 `%`、`_`、默认转义符的自动转义，禁止原始 SQL。当前不做同义词猜测；后续扩展要返回实际词项及匹配依据，不能静默将关键词搜索扩大成整个分类。

工具只接收下列业务参数，当前用户、当前账本及数据库连接由服务端绑定：

| 名称 | arguments 结构 | 输出约定 |
| --- | --- | --- |
| `summarize_entries` | `{filters, groupBy?}`，分组为 `category/date/account/creator` | 汇总、分组事实；按日期分组可生成趋势卡 |
| `search_entries` | `{filters, sort, cursor?, limit}`；排序 `date_desc/amount_desc`，默认每页 20，最大 20 | 账单卡、全部匹配的统计事实、匹配依据和 `nextCursor` |
| `compare_periods` | `{filters, previousRange, groupBy}`，默认按分类 | `filters.dateRange` 为本期，上期仅替换日期，其他条件一致；返回两期、差额及分组贡献 |
| `get_entry` | `{entryId}` | 权限范围内的单笔账单卡；无权和不存在采用不泄漏存在性的统一业务响应 |

第 03 步已实现键集分页：`date_desc` 按日期、时间、ID 降序，空时间排在同日已知时间之后；`amount_desc` 按金额、日期、ID 降序。签名游标绑定账号、账本、规范化筛选、排序和页大小，30 分钟有效，不能跨查询复用。需要分页时必须配置独立的 `CHAT_V2_CURSOR_SECRET`；每页重新读取，不承诺账单变动后的跨页冻结快照。

归一化模型决策 `ToolDecision`：

```json
{
  "action": "tool",
  "call": {
    "name": "search_entries",
    "arguments": {
      "filters": {
        "dateRange": {"start": "2026-10-21", "end": "2026-10-27"},
        "keywords": {"include": ["奶茶", "咖啡"], "mode": "any", "exclude": []}
      },
      "sort": "date_desc",
      "cursor": null,
      "limit": 20
    }
  },
  "text": null
}
```

一个决策只调用一次工具。结束时使用 `action=answer` 或 `clarify`，`call=null`，`text` 为非空正文；回答中的数字在第 06 步通过事实占位符校验。结构化循环和原生调用都归一化为此结构。

业务契约的 Pydantic JSON Schema 不能直接假设满足模型 strict 子集：第 05 步已在 `chat_v2_llm.strict_schema` 展开引用、将标签联合转为 `anyOf`、保留 nullable 并设置全部 required 和 `additionalProperties=false`，不删除后端业务校验。真实业务 schema 的上游兼容性仍在第 10 步验收。

第 05 步增加内部 `ContextPlanV2`（`patch/reference/referenceAction`）和 `FinishDecisionV2`（`action/text`）。筛选规划先于查询循环并计入模型预算；引用歧义直接返回候选，不猜测排除对象。原生路径将每次 function call 归一化为单个 `ToolDecision`，结构化模式直接生成该结构。结束正文只作为 `LoopOutcomeV2.answer_template`，仍需第 06 步可信校验，不能直接保存或发给客户端。

## 4. 工具结果、事实与卡片

`ToolResult` 为 `{callId, name, status, cards, factRefs, nextCursor, error, notices}`。`status` 为 `complete/partial/error`；失败必须有 `ToolError`，且不能同时提供卡片、事实或分页。查询零条记录属于成功结果，包含零总额和零笔数；服务不可用属于错误，不能降级成零结果。`notices` 传递实际日期截断等说明。

`ToolError.code` 为 `invalid_arguments/not_found/permission_denied/unavailable/limit`，`detail` 为可公开中文信息，禁止原始数据库和上游异常内容。

每个 `FactRef` 包含 `id/value/unit/label/sourceCallId`：

- `id` 在单轮内唯一，推荐 `q1.total`、`q2.difference`，只允许固定安全字符。
- `cents` 为整数分，可为负差额；`count` 为非负整数；`basis_points` 为百分数的基点整数，10000 表示 100%，可表达负变化率。
- `date` 为 ISO 日期字符串，`text` 为标签字符串。数值字段不接受浮点、布尔或数字字符串。
- 占位符格式为 `{{q1.total}}`，由服务器按单位格式化；基期为零时不给出增长比例事实。
- 卡片和正文引用都必须存在于本轮后端事实集合，不接受模型自行生成的事实值。当前 schema 检查卡片引用完整性，正文替换、标签可信与调用来源检查在第 03、06 步落实。

第 03 步工具生成的事实还包含 `metric/dateRange/previousRange/generatedAt`，分别标识数值角色、当前事实范围、比较范围及生成时间。工具结果已校验事实来源属于本次调用；新增字段对旧示例保持可空兼容。编排器为每次查询生成最长 32 字符的独立短 `callId`，不直接使用上游任意长的调用 ID。

`ResultCard` 包含 `id/type/title/filters/previousRange?/generatedAt/factIds/rows/footnote`。类型固定为 `summary/entries/trend/comparison`，预算和测算卡第二版再增加版本兼容定义。

每个 `ResultRow` 保留 `label/factIds/entryId?/date?/detail?`，第 03 步增加 `entryType/groupKey/categoryId/account/creatorId/time/matchedKeywords/isOther`。数值仍放在事实中。账单行必须包含真实 ID、日期、收支类型、分类、账户和记账人；趋势行必须有日期；比较卡必须有 `previousRange`，这些约束已落实。合并的其余分组使用 `isOther=true/groupKey=null`，不得当作真实账户或分类。

`generatedAt`、消息 `createdAt`、会话 `updatedAt` 均为非负 Unix 毫秒。列表展示上限不影响全部匹配记录的统计，明细备注按需返回。跨调用有账单更新时，不将不同时刻数据拼成一个严格归因结论。

可直接读取和校验的完整示例：[chat-v2-turn.json](examples/chat-v2-turn.json)。`tests/test_chat_v2_contract.py` 验证该示例及 SSE 载荷，后续变更契约时必须同步修改示例和测试。

## 5. 上下文、建议和持久化

`ChatContextV2` 包含 `schemaVersion=2`、当前 `filters`、可空 `previousRange`、`sources`、`resultRefs`、`summary` 和 `summaryThroughSequence`。

- `sources` 按字段路径记录来源：`explicit/inherited/default`；路径以 camelCase 为准，例如 `dateRange`、`keywords.include`。恢复历史字段时保存 `restoredFrom`，模型提出的来源和筛选由服务端重新验证。
- `resultRefs` 包含 `turnId/cardId/entryIds`，最多保留二十个引用。只能定位已保存且当前有权访问的数据，不能把快照中的金额直接作为最新值。
- `summary` 最长 2000 字符，第 04 步实现完整 JSON 行的滚动问答笔记，`summaryThroughSequence` 表明处理至该序号；较早笔记可以移出窗口，不覆盖原始问答，也不将旧结论当作最新账单事实。
- 第 04 步实现追加、替换、清空、重置和指定来源恢复，成功后存储完整的规范化筛选；自然语言到编辑的转换由第 05 步编排器接入，不让前端维护另一套继承规则。
- `Suggestion` 包含 `id/text/sourceTurnId/reference?`，最多三条。`reference` 与结果引用同形，点击时只提交 `suggestionId`，由服务端查回和验证。
- 第 06 步追问使用服务端受控模板，通常二至三条；没有合适目标或需要澄清时可更少。保存前及点击时核对同一会话的已保存来源、文字、卡片与当前账单权限；普通无卡汇总的建议从来源轮次上下文恢复范围。新轮不继承旧金额。
- `AnswerPreviewV2` 包含 `schemaVersion/answer/completeness/missing/cards/factRefs/suggestions`；完整结果 `missing=[]`，部分结果必须列明缺失内容。
- `TurnResponseV2` 在预览上增加 `id/sequence/text/context/createdAt`。持久化正文最长 2500 字符，与现有数据库字段保持一致，超过上限必须先缩短或明确失败，不能在保存时静默截断。
- `save_success` 仍只保存完整成功轮次；第 07 步的 `save_turn` 可保存明确部分消息，保留 `partial/missing`、清空可执行建议、递增轮次版本，但保留上一成功上下文。消息 `result` 沿用 `{schemaVersion:2,payload:AnswerPreviewV2,request:{suggestionId}}`，消息 `context` 为当时的权威成功状态。普通失败及提交前断连不保存半轮。
- 部分轮次不进入模型近期问答、摘要或自动账单引用，不能从该轮恢复筛选或执行建议；相同消息 ID 重试仍返回原部分消息，继续补查使用新 ID。`done + partial` 只表示缺失说明及部分结果已经保存。

第 06 步的 `AnswerServiceV2` 将循环结果转为 `PreparedAnswerV2.preview`：金额/笔数/比例只读本轮事实，日期取真实查询范围；模型自由文字不作为消费原因或节省承诺发布。未知引用、未验证数值降级为后端事实说明并标注 `partial/missing`。原始 `LoopOutcomeV2` 仍需经过该层，且绑定消息及原 deadline；第 07 步在相同期限内接入保存和 SSE，不能重新启动超时预算。

## 6. SSE 事件语义

HTTP 首包前校验登录、当前会话和账本权限，之后通过事件返回错误。沿用 `no-store`、`X-Accel-Buffering: no`、每十秒心跳、后端整轮超时预算和前端 300 秒等待。

| event | data | 客户端处理 |
| --- | --- | --- |
| `status` | `{text}` | 展示可公开的任务进度，不展示模型推理 |
| `snapshot` | `AnswerPreviewV2` | 完整替换当前轮预览，包括正文、事实和所有卡片 |
| `delta` | `{text}` | 追加经过校验的正文；收到新快照后，从新快照正文继续追加 |
| `ping` | `{}` | 保活，不产生聊天消息 |
| `done` | `TurnResponseV2` | 覆盖预览，确认已保存，释放当前发送状态 |
| `error` | `{status, detail}` | 本轮未确认完成；保留原消息 ID 和未确认预览，供读取/重试 |

`complete/partial` 描述业务答案是否完整；`done` 描述保存是否完成，两者不能混为一谈。前端不因收到统计卡而判定保存成功。所有卡片引用的事实必须随同一个快照发送。

## 7. 模型能力探测与执行方式

从 `backend` 目录运行，默认不会读取真实配置或创建网络客户端：

```powershell
.\.venv\Scripts\python.exe -m app.integrations.llm_probe
```

显式启用真实请求：

```powershell
.\.venv\Scripts\python.exe -m app.integrations.llm_probe --live
```

最多五次模型请求：结构化输出一次，原生工具调用及结果回传两次，正文流一次，取消测试一次。不访问 PostgreSQL、Redis、用户账单或聊天历史，不自动重试付费请求。取消测试申请一段较长的无业务测试文本，收到首段正文后取消任务并等待流关闭。

命令输出固定使用 UTF-8，仅报告状态和耗时，不打印密钥、工具原始返回、推理或上游错误正文。每项受现有 `CHAT_TIMEOUT_SECONDS` 限制，原生工具两次请求共用该项预算。整体最坏耗时为四项预算之和。

状态：`passed` 代表请求和内容均符合本探测；`rejected` 代表上游拒绝原生工具请求，不能据此断言所有工具能力永久不可用；`failed` 代表超时、服务错误或格式不符合；默认运行显示 `live=false` 和 `recommendation=pending`。全部通过退出码为 0，任一不通过为 1，配置错误为 2，用户中断为 130。

能力决策：基础结构化、流和取消均通过时，原生工具往返通过则建议 `native`；原生工具未通过则可显式选择 `structured` 作为后续开发路径。基础能力不通过时为 `pending`。不在真实聊天里捕获失败后自动改模式重发。

预留配置默认值：

| 配置项 | 默认值 | 用途 |
| --- | --- | --- |
| `CHAT_V2_ENABLED` | `false` | 第 07 步新增，业务路由启用开关；能力端点仍需登录并返回实际状态 |
| `CHAT_V2_TOOL_MODE` | `structured` | 保守默认，具体部署验证后显式选择 `native` 或 `structured` |
| `CHAT_V2_MAX_TOOL_CALLS` | `6` | 单轮累计工具调用数 |
| `CHAT_V2_MAX_MODEL_REQUESTS` | `6` | 单轮累计模型请求数，包括最终回答，不表示允许六次查询后再额外回答 |
| `CHAT_V2_MAX_RANGE_DAYS` | `366` | 单个查询区间的最大自然日数，包含首尾 |
| `CHAT_V2_CURSOR_SECRET` | 空 | 第 03 步新增，分页 HMAC 专用密钥，至少 32 字符；启用分页前配置，各环境独立 |
| `CHAT_V2_CONTEXT_MAX_CHARS` | `16000` | 第 04 步新增，历史上下文 JSON 字符预算，4000—32000，不是 token 数 |
| `CHAT_V2_CONTEXT_RECENT_TURNS` | `6` | 第 04 步新增，最近完整问答轮数，1—10 |
| `CHAT_V2_MAX_INPUT_CHARS` | `220000` | 第 05 步新增，完整模型 HTTP 请求 JSON 字符预算，包含 schema、提示与工具续接 |
| `CHAT_V2_MAX_TOOL_RESULT_CHARS` | `60000` | 第 05 步新增，单个完整工具结果字符上限 |
| `CHAT_V2_MAX_TOOL_OUTPUT_CHARS` | `160000` | 第 05 步新增，单轮累计完整工具结果字符上限 |
| `CHAT_V2_MAX_RESPONSE_BYTES` | `262144` | 第 05 步新增，单次模型响应读取字节上限 |
| `CHAT_V2_MAX_OUTPUT_TOKENS` | `12000` | 第 05 步新增，模型输出 token 上限，含上游适用的推理预算 |

这些配置已能读取和校验，第 02 步已接入日期跨度，第 03 步接入签名分页，第 04 步接入上下文预算，第 05 步接入工具循环限额。规划、查询决策和最终草稿共用模型次数，整轮共用 `CHAT_TIMEOUT_SECONDS`；达到规模上限时舍弃超限工具结果，普通失败不能解释成零消费。第 07 步已注册路由但默认关闭，真实配置未改；V1 及一句话记账保持原接口。

### 2026-10-27 实际探测

上海时间 17:19 完成，使用本地现有配置、`gpt-6-luna`、Responses、`xhigh`。五次模型请求，没有业务数据和数据库操作。

| 项目 | 结果 | 总耗时 |
| --- | --- | --- |
| 严格结构化请求与 echo 校验 | 通过 | 4547 ms |
| 强制原生工具调用及新随机结果回传 | 通过 | 4218 ms，两次请求合计 |
| 结构化正文流及完成事件 | 通过 | 2250 ms |
| 收到正文后取消读取任务并关闭流 | 通过 | 36250 ms，包含等待首段正文的时间 |

本地链路的后续主路径选为原生工具调用，结构化循环为可显式配置的兼容路径。未修改真实 `.env` 或自动切换现有聊天。

限制：只验证无业务数据的 echo schema；尚未验证实际账单工具 schema、多轮业务效果、并行工具、工具参数流式增量和 Chat Completions 的真实链路。取消结果只证明本地任务传播取消、HTTP 流完成清理，不能证明中转站或模型停止生成和计费。耗时为单次探测样本，不是性能承诺。

原生调用遵循[官方 Function calling 文档](https://developers.openai.com/api/docs/guides/function-calling)：工具结果按调用 ID 回传，Responses 无状态续接保留工具调用相关 output，strict 参数对象显式限制额外字段。协议兼容性仍以实际中转链路探测为准。

## 8. 验证与后续任务

默认离线检查自动包含新增测试，不需要设置真实模型开关：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_chat_v2_contract.py tests/test_llm_probe.py -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

`test_llm_probe.py` 对两种协议模拟正常返回、原生工具请求被拒绝、错误工具参数、服务异常、超时、断流、无效 JSON 和取消清理；`test_chat_v2_contract.py` 验证示例、SSE、引用完整性、整数单位、额外身份字段拒绝与旧路由保持。

第 02 步的具体规则和验证见[日期与组合筛选](chat-v2-filters.md)，第 03 步见[只读查账工具与事实输出](chat-v2-tools.md)，第 04 步见[可修正的对话上下文](chat-v2-context.md)，第 05 步见[有边界的多步工具循环](chat-v2-loop.md)，第 06 步见[可信回答与动态追问](chat-v2-answer.md)，第 07 步见[V2 会话、历史与 SSE](chat-v2-api.md)，第 08 步见[前端状态与流处理](../../frontend/docs/chat-v2-state.md)。第 09 步实现与本地验证见[专用结果卡、账单入口及阅读交互](../../frontend/docs/chat-v2-cards.md)，微信设备验收待补；第 10 步[真实场景评测](chat-v2-evaluation.md)已完成，原生分批最终覆盖 39/39 表达、兼容冒烟复测 3/3，下一步为第 11 步验收及发布准备。


2026-11-05 后续发布记录：用户要求更新服务端后，V2 运行代码已随 [`20261105T072604Z`](releases/20261105T072604Z.md) 上线；实际 V2 开关仍为默认关闭，分页密钥未配置，小程序未上传。发布保留原管理员实现及 `0004_shared`，未执行迁移。前述各步的“未部署”指开发验收当时状态。
