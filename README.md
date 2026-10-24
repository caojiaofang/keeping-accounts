# 墨记

把日子，一笔一笔记好。一款基于 uni-app 的微信记账小程序，规划接入大模型，实现一句话记账和对话消费分析。

## 技术选型

- 前端：uni-app + Vue 3 + TypeScript + Pinia，使用 HBuilderX 开发。
- 后端：Python + FastAPI。
- 数据库：PostgreSQL，复用 z11 服务器上的现有服务，开发库为 `keeping_accounts_dev`，正式库为 `keeping_accounts_prod`，两个库均已创建。
- 缓存与临时状态：Redis，复用 z11 上的现有服务。
- 大模型：由后端调用配置的 sub2api 中转站。
- 微信小程序 AppID：`wx0f90babab478b2b6`。

## 项目结构

```text
keeping-accounts/
├── frontend/           # HBuilderX 标准目录结构的小程序前端
├── backend/            # FastAPI 后端基础框架，使用 PyCharm 开发
├── docs/               # 功能需求与设计说明
└── 项目方案说明.md      # 技术选型、功能范围和开发约定
```

## 当前进度

前端已实现明细、统计、消费日历、账本、多人账本、我的和手动记账等页面，支持本地新增、编辑、删除、预算设置及 CSV 复制导出。消费日历可同时展示每天的支出和收入，也可选择单项，可查看当天账单并按选定日期补记。

多人账本提供创建、家庭示例、受邀加入预览、成员管理、按成员筛选与记账人展示等本地交互；暂不支持真实跨设备邀请和同步。后端实施约定见[多人账本需求](docs/多人账本需求.md)。

「墨记助手」包含一句话记账和聊天分析两个演示模式：预置记账示例可生成草稿，修改后逐笔或批量保存；聊天分析可展示本地账单的分类分布、明细、月度对比和连续追问。

**微信登录已接入，记账数据仍保存在设备本地。** 首次打开可微信登录或选择体验：账号拥有独立的空账本与本地记录，体验区保留原有演示账单。云端账单、账本 API 和真实大模型尚未接入。

## 开发与检查

在 HBuilderX 中导入 `frontend` 目录，安装依赖后运行到微信开发者工具。详细步骤见[前端说明](frontend/README.md)。

```powershell
cd frontend
npm install
npm run type-check
npm run check:assistant
npm run build:mp-weixin
```

后端已直连接入 z11 的 `keeping_accounts_dev` 开发库与 Redis 逻辑库 1，并实现微信小程序登录、当前用户查询和退出登录。开发库已创建用户表，前端已接入登录页、状态恢复、退出和账号缓存隔离。在 PyCharm 中选择 `backend/.venv/Scripts/python.exe` 解释器、安装依赖后，直接运行 `backend/run.py`；本机认证已配置，启动时检查 PostgreSQL 和 Redis 连接，默认访问 `http://127.0.0.1:8000/docs` 查看接口文档。详细步骤见[后端说明](backend/README.md)和[微信登录接口说明](backend/docs/wechat-login.md)。

后续开发依据见[项目方案说明](项目方案说明.md)。真实密钥和数据库连接配置通过后端环境变量注入，不提交到仓库。

[微信登录需求](docs/微信登录需求.md)的后端接口已实现，通过微信 OpenID 识别唯一用户并管理登录状态；前端登录页、登录恢复、退出及用户缓存隔离均已接入。
