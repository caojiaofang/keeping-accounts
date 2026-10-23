# 墨记

基于 uni-app、Vue 3、TypeScript 和 Pinia 的微信记账小程序前端。使用 **HBuilderX 标准项目目录**，直接导入本目录即可开发。微信 AppID：`wx0f90babab478b2b6`。

## 在 HBuilderX 中运行

1. 使用支持 Vue 3 的 HBuilderX 正式版，通过「文件 → 导入 → 从本地目录导入」选择 `frontend`，不要选择整个仓库或 `unpackage`。
2. 第一次运行先在此目录执行 `npm install`，安装项目依赖。
3. 在 HBuilderX 的「工具 → 设置 → 运行配置」中设置微信开发者工具路径，并在微信开发者工具的「设置 → 安全设置」中开启服务端口。
4. 在 HBuilderX 中选择「运行 → 运行到小程序模拟器 → 微信开发者工具」。编译目录为 `unpackage/dist/dev/mp-weixin`。
5. 微信开发者工具中使用拥有此 AppID 开发权限的微信账号登录。页面无需后端或 AppSecret 即可体验。

如 HBuilderX 提示缺少 Sass 编译插件，按提示安装。建议使用与本项目锁定的 uni-app CLI 编译器相同或更新的 HBuilderX 正式版；构建依赖以 `package-lock.json` 为准。

## 目录结构

```text
frontend/
├── common/              # 类型、金额与日期工具、演示数据、公共样式
├── components/          # 导航、账单列表、图表和弹层等组件
├── pages/               # 明细、统计、账本、我的、记一笔、墨记助手
├── static/              # 本地 SVG 图标，不依赖远程图片
├── store/               # Pinia 状态和本地存储
├── unpackage/           # HBuilderX / CLI 编译产物，不提交版本控制
├── scripts/             # 可选 CLI 构建与图标生成工具
├── App.vue
├── main.js              # HBuilderX 标准启动入口，业务代码使用 TypeScript
├── manifest.json        # 微信 AppID 在 mp-weixin.appid 中配置
├── pages.json
├── package.json
└── uni.scss
```

`manifest.json` 顶层 `appid` 是 HBuilderX 生成的 DCloud 应用标识，并非微信 AppID。微信 AppID 已配置在 `mp-weixin.appid`。

## 已实现的本地交互

- 明细：月份与账本切换、收入/支出切换、分类筛选、备注/账户搜索。
- 记账：自定义金额键盘、分类、日期、账户、账本、备注；新增、编辑、确认删除。
- 统计：分类环形图、日均金额、七日趋势和分类排行；数据与本地账单同步。
- 账本：账本切换、新建账本、独立收支与预算。
- 我的：昵称、预算、分类一览、复制当前账本全部账单为 CSV、使用帮助和恢复演示数据。
- 墨记助手：一句话记账和聊天分析两个页面模式，从首页、记账页或统计页进入。

## 墨记助手静态演示

首页新增「一句话记账」和「聊聊花费」入口，助手内可随时切换模式。当前没有调用大模型，也没有发送数据到外部服务。

- 一句话记账：选择早餐、多笔消费或兼职收入示例，再点击「识别账单」生成预置草稿。支持修改金额、收支类型、分类、日期、时间、账户和备注，逐笔确认、批量确认或忽略。确认后写入现有本地账本，明细、统计和 CSV 导出同步更新，已确认的草稿不会重复保存。
- 示例「今天早上八点吃早餐8块钱」包含 `08:00` 时间。未给出时间的账单不补造时间，未给出账户时明确使用默认账户。现有账单兼容可选时间字段。
- 聊天分析：点选示例问题，展示当前账本的分类占比、分类明细、月度对比、超过200元的支出和消费提示。回答根据真实的本地账单计算，连续追问可继承分类范围。统计页进入时保留所选月份，首页进入时使用当前月份。
- 任意自然语言识别与自由聊天等待后端接入，未支持的输入会说明演示范围，不伪造模型回答。对话仅保留在本次页面中，切换助手模式会保留内容，离开后重新进入则开始新对话。

演示逻辑位于 `common/assistant-demo.ts`，交互组件位于 `components/assistant/`。后续使用 Python + FastAPI + MySQL 后端接入 sub2api，按项目根目录的[项目方案说明](../项目方案说明.md)实施。

首次运行以当天为基准生成演示账单。此后读写本地存储，不会反复填充或重置数据。金额以整数分保存。当前账户仅是账单标签，不管理资金余额；预算为各账本的固定月预算。

**当前没有微信登录、云端同步、后端 API 或多人共享。** 所有业务操作均在本地完成。清理存储或卸载会丢失数据，可通过「我的 → 导出账单」复制 CSV 留存。「恢复演示数据」需要确认，确认后会覆盖现有本地账单、账本及预算。

## 可选的命令行检查

这些命令与 HBuilderX 使用同一份根目录源码，仅作为构建验证和辅助预览，无需将项目改成 `src` 目录结构。

```powershell
npm install
npm run type-check
npm run check:assistant
npm run dev:mp-weixin
npm run build:mp-weixin
```

开发模式输出 `unpackage/dist/dev/mp-weixin`，发布构建输出 `unpackage/dist/build/mp-weixin`，可手动导入微信开发者工具。H5 仅辅助检查移动布局，微信开发者工具和真机才是小程序验收环境。

```powershell
npm run dev:h5
npm run build:h5
```

本地图标可通过 `npm run generate:icons` 重新生成。
