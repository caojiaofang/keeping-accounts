# 墨记

把日子，一笔一笔记好。一款基于 uni-app 的微信记账小程序，已接入大模型，实现一句话记账和对话消费分析。

## 技术选型

- 前端：uni-app + Vue 3 + TypeScript + Pinia，使用 HBuilderX 开发。
- Web 管理端：Vue 3 + Element Plus + TypeScript + Vite，独立构建与部署。
- 后端：Python + FastAPI。
- 数据库：PostgreSQL，复用 z11 服务器上的现有服务，开发库为 `keeping_accounts_dev`，正式库为 `keeping_accounts_prod`，两个库均已创建。
- 缓存与临时状态：Redis，复用 z11 上的现有服务。
- 大模型：由后端调用配置的 sub2api 中转站。
- 微信小程序 AppID：`wx0f90babab478b2b6`。

## 项目结构

```text
keeping-accounts/
├── frontend/           # HBuilderX 标准目录结构的小程序前端
├── admin/              # Vue 3 + Element Plus 独立 Web 管理端
├── backend/            # FastAPI 后端基础框架，使用 PyCharm 开发
├── docs/               # 功能需求与设计说明
└── 项目方案说明.md      # 技术选型、功能范围和开发约定
```

## 当前进度

后台服务端和 Web 管理端已于 2026-11-06 [更新至 `20261106T115227Z`](backend/docs/releases/20261106T115227Z.md)，提供管理员登录、数据概览、用户查询、账本及成员只读查询，并启用账号设置与登录日志。原管理员密码保留，旧会话需重新登录。前端使用 Vue 3 + Element Plus，独立打包与部署；页面地址为 `https://mjzs.zf-yun.cn`，管理接口前缀为 `/prod-api`，小程序原有 `/api/v1` 保留。管理员独立于微信用户，已从原环境摘要初始化到独立账号表，没有默认密码。开发见 [管理端说明](admin/README.md)，接口与部署见 [Web 管理端部署](backend/docs/web-admin.md)。

前端已实现明细、统计、消费日历、账本、多人账本、我的和手动记账等页面，支持新增、编辑、删除、预算设置及 CSV 复制导出。消费日历可同时展示每天的支出和收入，也可选择单项，可查看当天账单并按选定日期补记。

「家庭预算」按[分步开发计划](docs/家庭预算功能与分步开发计划.md)推进，第 01—11 步已完成方案、总览、[项目执行／目标详情](frontend/docs/family-budget-item.md)及[场景联调与回归](backend/docs/family-budget-regression.md)。支持实际存入／修正、原账单关联、正常记账返回、关闭目标和余款续建。43 项预算检查、34 项隔离 PostgreSQL 集成（含 8 组真实前端 HTTP 场景）、统一检查及 H5／小程序构建通过。第 12 步进行中：服务端和后台 Web 已发布，正式库已迁移至 `0006_budget`，生产只读验收通过；开发库本次未升级，小程序未上传，微信工具与真机验收待完成。

多人账本已接入真实云端接口：支持邀请预览与确认加入、成员权限、共同收支、修改记录、退出、转让及解散；体验模式保留家庭示例。开发库已应用 `0004_shared`，正式库现为 `0006_budget`，正式后端为 `20261106T115227Z`。小程序体验版 `1.0.9` 已发布；2026-10-26 用户确认双账号真机测试已完成。实现与验收见[共享账本说明](backend/docs/shared-books.md)。

「墨记助手」一句话记账已接入 sub2api，使用 `gpt-6-luna`，推理强度固定为 `xhigh`：自然语言生成可编辑草稿，确认后保存到云端。聊天分析已支持云端账单查询、连续追问、同期比较及历史会话；体验模式保留本地示例。

「聊聊花费」智能助手升级按[功能说明与分步开发计划](docs/聊聊花费智能助手开发计划.md)推进，拆为十四个可独立验收的步骤。第 01 步的 [V2 契约与模型探测](backend/docs/chat-assistant-v2.md)已随后端 `20261027T092843Z` 发布。第 02 步[日期与组合筛选](backend/docs/chat-v2-filters.md)已以 `20261028T031201Z` [发布](backend/docs/releases/20261028T031201Z.md)。第 03—07 步已完成[只读工具](backend/docs/chat-v2-tools.md)、[可修正上下文](backend/docs/chat-v2-context.md)、[有限循环](backend/docs/chat-v2-loop.md)、[可信回答与追问](backend/docs/chat-v2-answer.md)及[V2 会话、历史与 SSE](backend/docs/chat-v2-api.md)。2026-11-05 第 08 步已接入[前端状态与流处理](frontend/docs/chat-v2-state.md)，34 项聊天检查、小程序与 H5 构建通过，支持受控回退、跨月份历史、可靠重试及旧会话只读续聊。第 09 步已完成[专用结果卡、账单入口与阅读交互](frontend/docs/chat-v2-cards.md)的开发和本地验收，38 组聊天检查、9 组云端共享检查、H5 实际点击及小程序/H5 构建通过；微信设备验收待补。第 10 步已完成[场景评测与真实联调](backend/docs/chat-v2-evaluation.md)：原生模式分批最终覆盖 13 类、39 个表达，三组六轮及断连重试通过，兼容冒烟复测 3/3；首轮失败与修复记录保留。统一检查后端 481 passed、14 skipped，小程序/H5 构建通过。微信模拟器布局与入口已补验，完整设备验收仍待补。V2 服务端已于 2026-11-05 按用户指令[发布至 `20261105T072604Z`](backend/docs/releases/20261105T072604Z.md)，随后按用户指令于北京时间 16:52 [启用 V2](backend/docs/releases/20261105T084818Z.md)，采用 native 模式与独立分页密钥，V1 保持兼容。用户已将新版小程序提交审核；第 11 步的完整设备验收仍待补。

2026-10-27 已增加聊天式记账与单张图片识别：支持连续补充金额、日期及账户，拍照或相册识别截图/小票，核对草稿后确认保存。后端、Pillow 依赖及图片接口 Nginx 配置已发布至 `20261027T052736Z`，生产容器真实模型验收通过；小程序新界面尚未上传体验版，见[文字聊天与图片记账](backend/docs/conversational-record.md)及[上线记录](backend/docs/releases/20261027T052736Z.md)。

**个人云端记账与智能识别已接入。** 登录后账本、账单和预算保存到服务端，明细、统计及日历使用同一份数据；体验区保留本地演示。旧账号本机记录不会自动上传，可在「我的」导出 CSV。正式库为 `0006_budget`，正式后端版本为 `20261106T115227Z`，共享邀请密钥已配置。接口、模型配置和验收见[云端记账与智能识别](backend/docs/ledger-assistant.md)。

## 开发与检查

在 HBuilderX 中导入 `frontend` 目录，安装依赖后运行到微信开发者工具。详细步骤见[前端说明](frontend/README.md)。

```powershell
cd frontend
npm ci
cd ..
cd admin
npm ci
cd ..
.\scripts\check.ps1
# 发布前同时验证管理端、微信小程序和 H5 构建。
.\scripts\check.ps1 -Build
```

统一入口要求先安装小程序、管理端依赖及 `backend/.venv` 中的后端开发依赖。默认依次执行小程序 TypeScript 检查和全部七个功能检查脚本、管理端 TypeScript 检查，以及后端 pytest、Ruff 代码与格式检查、`pip check`；任一失败立即停止并返回非零退出码，可从任意工作目录通过脚本路径调用。`-Build` 会在检查通过后追加管理端、小程序和 H5 三种构建。

统一入口运行期间将 `MOJI_RUN_INTEGRATION_TESTS` 和 `MOJI_RUN_LLM_TESTS` 临时设为 `0`，结束后恢复，不访问真实数据库、Redis 或模型。需要真实集成验证时使用各功能文档中的专用命令。仅检查前端可在 `frontend` 目录执行 `npm run check`，各单项命令仍可独立使用。

后端已直连接入 z11 的 `keeping_accounts_dev` 开发库与 Redis 逻辑库 1，并实现微信小程序登录、当前用户查询和退出登录。开发库已创建用户、账本与账单表，前端已接入登录、个人云端账本及账号缓存隔离。在 PyCharm 中选择 `backend/.venv/Scripts/python.exe` 解释器、安装依赖后，直接运行 `backend/run.py`；本机认证已配置，启动时检查 PostgreSQL 和 Redis 连接，默认访问 `http://127.0.0.1:8000/docs` 查看接口文档。详细步骤见[后端说明](backend/README.md)和[微信登录接口说明](backend/docs/wechat-login.md)。

后续开发依据见[项目方案说明](项目方案说明.md)。真实密钥和数据库连接配置通过后端环境变量注入，不提交到仓库。

[微信登录需求](docs/微信登录需求.md)的后端接口已实现，通过微信 OpenID 识别唯一用户并管理登录状态；前端登录页、登录恢复、退出及用户缓存隔离均已接入。
