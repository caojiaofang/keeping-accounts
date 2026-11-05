# 聊聊花费 V2：有边界的多步工具循环

2026-11-04，第 05 步交付。实现位于 `app/services/chat_v2_loop.py` 和 `app/integrations/chat_v2_llm.py`。本步将已有[上下文服务](chat-v2-context.md)与[只读工具](chat-v2-tools.md)接入内部编排，不注册 API 或自动保存回答。

## 一轮怎样执行

1. 校验账号、当前会话、账本权限及消息结构，读取最新成功上下文并释放事务。
2. 模型提出 `ContextPlanV2`：受控 `patch` 和可选的账单 `reference/referenceAction`。该请求计入模型请求总数。
3. 服务端核对编辑和来源，解析引用，再生成 `ContextDraft`。引用不唯一或已失效时直接返回候选，不继续猜测或查询；新的排除对象必须经过引用解析，不能直接猜一个已展示 ID。
4. 模型选择一次只读工具，服务端校验名称、参数、日期及权限，用服务端绑定的账本执行。调用编号固定为本轮唯一的 `q1/q2/...`，与上游调用 ID 分开。
5. 完整、大小合规且来源正确的结果作为数据回传，模型选择继续查询、澄清或提出回答草稿；每次返回前重新检查会话版本及账本权限。
6. 返回内部 `LoopOutcomeV2`，交给第 06 步做可信正文、卡片和追问处理，再由第 07 步接入消息保存与 SSE。

简单问题可只查汇总；“为什么多花了”可以依次汇总、比较和查相关明细，没有固定必须跑满的工具链。规划器和查询模型都得到后端上海业务日期及规范化上下文；日期计算、实际账单筛选和金额统计仍由后端校验执行。

模型等待前、工具结束后都释放只读事务。查询前后及返回前复核当前权限；即使已查到结果，成员失权或会话被其他请求更新后也不返回旧结果。工具中途变化的账单不被历史快照替代；不同工具的生成时间仍保留，后续回答层需处理跨查询变化。

## 两种模型模式

`CHAT_V2_TOOL_MODE=native` 为探测通过后的主路径，`structured` 是显式兼容模式。仍保留保守默认 `structured`，真实环境配置未自动修改。一次失败不会偷偷切换模式或重试付费请求。

两种模式均支持现有 `Responses` 与 `Chat Completions` 适配，继续使用配置中的 `gpt-6-luna` 和 `xhigh`。第 05 步当时只完成模拟传输检查。2026-11-05 第 10 步已完成[真实场景评测](chat-v2-evaluation.md)：原生模式分批最终覆盖 39/39 表达，结构化兼容模式完成比较场景的三种表达冒烟复测；后者不是全部场景覆盖。

原生路径按 [OpenAI 官方 Function calling 文档](https://developers.openai.com/api/docs/guides/function-calling)实现工具调用与结果 ID 对应、严格参数对象、关闭并行调用，以及 Responses 无状态续接时保留相关 output。Responses 请求 `store=false`，续接包含 reasoning 的加密状态；这些状态不展示、不写日志、不持久化到聊天。

本地适配会展开 Pydantic `$ref`、将标签联合转为 `anyOf`、移除默认值和 discriminator 元数据，所有对象属性显式必填并拒绝额外字段，已有 nullable 保留。模型 schema 不能替代业务校验：名称、字段、金额、日期、页大小、身份注入和参数关联规则仍在后端重新验证。

原生模式的最终结构为 `FinishDecisionV2`，只允许 `answer/clarify` 和正文草稿。结构化模式使用既有 `ToolDecision`，每次只允许一个工具调用或一次结束决策。工具次数耗尽时明确禁用后续工具，仍可在剩余模型预算内生成结束草稿。上游意外返回并行调用、重复调用 ID、拒绝、无效 JSON 或未正常完成状态时停止，不执行半个批次。

## 预算与终止

| 配置 | 默认值 | 实际约束 |
| --- | --- | --- |
| `CHAT_V2_MAX_MODEL_REQUESTS` | 6 | 筛选规划、每次决策、最终草稿都计数；不额外赠送结束请求 |
| `CHAT_V2_MAX_TOOL_CALLS` | 6 | 每次真正执行工具均计数，失败也计数 |
| `CHAT_TIMEOUT_SECONDS` | 240 | 整轮共同期限，包含上下文读取、规划、模型等待、工具执行和权限复核 |
| `CHAT_V2_MAX_INPUT_CHARS` | 220000 | 每次完整 HTTP JSON 请求的 Unicode 字符数，包含提示词、schema、历史、工具回传及续接状态；范围 20000—500000 |
| `CHAT_V2_MAX_TOOL_RESULT_CHARS` | 60000 | 单个完整工具结果的 JSON 字符数；范围 4000—200000 |
| `CHAT_V2_MAX_TOOL_OUTPUT_CHARS` | 160000 | 单轮累计接收的完整工具结果字符数；范围 4000—400000 |
| `CHAT_V2_MAX_RESPONSE_BYTES` | 262144 | 每次模型响应正文按解压后字节累计读取；范围 8192—2097152，超限关闭连接 |
| `CHAT_V2_MAX_OUTPUT_TOKENS` | 12000 | Responses 的 `max_output_tokens` / Chat Completions 的 `max_completion_tokens`；范围 1024—32000，包含上游适用的推理输出预算 |

字符预算不是 token 估算，不能据此推算费用。上下文层的最近轮数及历史预算继续独立生效，工具筛选的最大日期跨度仍由第 02 步控制。默认六次模型请求下，规划、四次查询决策和一次最终草稿已用满预算，不代表一定能进行六次工具调用后再回答。

重复保护在执行前检查规范化工具名和参数：集合顺序、重复词及默认值不改变同一查询的判定；分页游标不同则是新的查询。重复调用直接停止，不再尝试让模型反复纠错。

结果超限时整份舍弃该工具结果，不截断数值、卡片行或事实引用。模型 HTTP 断流、超限和异常 JSON 都不会产生可用的半个决策。取消直接向上传播，关闭活动 HTTP 读取或取消活动工具，之后不再启动模型请求。

## 返回值及失败语义

`ChatLoopV2.run(conversation_id, SendMessageV2)` 返回 `LoopOutcomeV2`：

- `draft`：规范化候选上下文及期望会话版本，尚未写入成功历史。
- `action`：`answer/clarify/partial`；`answer_template` 仍是待校验正文。
- `tool_results`：只有本轮已完整验证、未超规模限制的工具结果。
- `missing`：部分结果的缺失原因；`model_requests/tool_calls` 是本轮计数，`reference` 可携带歧义候选。
- `requires_answer_validation=true`：必须经第 06 步校验，不能直接转换为成功预览、发给客户端或保存。

尚无工具事实时拒绝直接输出消费结论，但允许提出待校验的澄清问题。成功查询的零总额属于事实；服务异常属于错误，不能伪装成没有记录。

已经有完整结果时，后续模型失败、重复查询、工具失败或次数/规模耗尽可返回 `partial`，只保留此前验证的结果和后端固定说明，不保留失败步骤的猜测。返回前仍须通过权限和版本检查。整轮期限到期则返回 504，不在到期后追加查询来放行旧结果；权限失效和并发版本变化直接失败并丢弃返回路径中的旧结果。

本服务不保存任何轮次，普通失败、取消和部分结果都不会覆盖成功上下文。第 06 步已接入[可信回答与动态追问](chat-v2-answer.md)，带建议 ID 的调用先从同一会话已保存轮次查回、校验文字及引用，恢复来源范围，跳过一次模型规划请求。未保存或无权访问的建议不能执行。

日志只记录受控工具名称、完成状态、耗时和结果行数，不记录账单备注、聊天原文、模型推理、调用参数或密钥。

## 调用与验证

服务内部接入示例（复用当前请求的 `LedgerService` 和 HTTP 客户端）：

```python
store = ContextStoreV2(ledger, settings, today=business_today())
model = ChatModelV2(LlmClient(settings, http_client))
outcome = await ChatLoopV2(store, model).run(conversation_id, message)
# 下一步交给可信回答层，不直接调用 save_success。
```

离线检查在 `backend` 下运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_chat_v2_loop.py tests/test_chat_v2_loop_limits.py -q
```

测试通过 HTTP 内存传输模拟两种协议和两种模式，执行真实内存 SQL 工具与上下文服务，覆盖简单/多步分析、草稿编辑、引用歧义与排除、调用 ID 续接、严格 schema、重复/非法工具、次数及输出上限、无事实、零记录与故障区分、断流、超时、取消、权限/版本变化及注入式备注。注入测试证明数据隔离和工具边界，不等同于真实模型对所有攻击均能正确理解；真实业务效果现已通过第 10 步[分批评测](chat-v2-evaluation.md)记录，早期失败与修复后的结果分开保留。

第 05 步验收时未发送真实模型请求，未连接开发或正式数据库、Redis，未构建、迁移、发布服务或上传小程序。后续第 06 步已完成[可信回答与动态追问](chat-v2-answer.md)，下一步为第 07 步 API、持久化与 SSE 接入。
