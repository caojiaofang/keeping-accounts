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
├── backend/            # 后端目录，目前仅包含开发说明
└── 项目方案说明.md      # 技术选型、功能范围和开发约定
```

## 当前进度

前端已实现明细、统计、消费日历、账本、我的和手动记账等页面，支持本地新增、编辑、删除、预算设置及 CSV 复制导出。消费日历展示每天的支出，可查看当天账单并按选定日期补记。

「墨记助手」包含一句话记账和聊天分析两个演示模式：预置记账示例可生成草稿，修改后逐笔或批量保存；聊天分析可展示本地账单的分类分布、明细、月度对比和连续追问。

**当前为本地交互演示版本。** 首次打开包含模拟账单，数据保存在设备本地。后端 API、PostgreSQL 持久化、Redis、微信登录及真实大模型调用尚未接入，自由输入的识别与聊天能力等待后端接入。

## 开发与检查

在 HBuilderX 中导入 `frontend` 目录，安装依赖后运行到微信开发者工具。详细步骤见[前端说明](frontend/README.md)。

```powershell
cd frontend
npm install
npm run type-check
npm run check:assistant
npm run build:mp-weixin
```

后续开发依据见[项目方案说明](项目方案说明.md)，后端进度见[后端说明](backend/README.md)。真实密钥和数据库连接配置通过后端环境变量注入，不提交到仓库。
