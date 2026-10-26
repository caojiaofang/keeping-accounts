# 墨记 Web 管理端

独立的 Vue 3 + TypeScript + Vite + Element Plus 管理端，使用真实 FastAPI 接口。正式页面位于 `https://mjzs.zf-yun.cn`，管理 API 前缀为 `/prod-api`，小程序继续使用原有 `/api/v1`。

## 第一版范围

- 管理员账号密码登录、登录恢复、退出和登录过期处理。
- 数据概览：用户数、今日新增用户、近七天登录用户、使用中账本、共享账本、有效账单，以及近十四天新增趋势。
- 用户查询：用户 ID 搜索、数据库分页、注册时间、最近登录、持有账本数、有效账单数、用户详情。
- 账本只读查询：名称或 ID 搜索、类型、状态、持有人筛选、分页、账本详情与成员记录。
- 加载、错误重试、空列表、复制 ID、手机窄屏导航和响应式布局。

第一版仅有一个独立的平台管理员账号，不提供公开注册。业务查询不显示 OpenID、账单正文、金额或聊天内容，也不提供编辑和删除操作。

## 本地启动

要求 Node.js >= 22.12 和现有 Python 3.12 后端环境。在仓库根目录的 PowerShell 执行：

```powershell
.\backend\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\backend\.venv\Scripts\python.exe backend/admin_password.py
```

将生成的 `ADMIN_PASSWORD_HASH` 写入不提交的 `backend/.env`，并配置：

```dotenv
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH='<生成的 Argon2 摘要>'
ADMIN_ORIGIN=http://127.0.0.1:5174
ADMIN_SESSION_TTL_SECONDS=28800
ADMIN_LOGIN_RATE_LIMIT=5
```

不要将明文密码、摘要或数据库配置放入前端。实际摘要使用单引号包裹，保留其中的 `$` 字符。后端需要可用的 PostgreSQL 和 Redis；未配置管理员摘要时禁止管理员登录，没有默认密码。

```powershell
# 终端一：启动后端；已有进程需重启以读取新环境变量。
.\backend\.venv\Scripts\python.exe backend/run.py
# 终端二：启动独立 Web 管理端。
cd admin
npm ci
npm run dev
```

使用 `http://127.0.0.1:5174` 访问。Vite 将 `/prod-api` 代理到 `127.0.0.1:8000`，无需浏览器跨域配置。访问来源须与 `ADMIN_ORIGIN` 完全一致；改用 localhost、其他主机或端口时，应同步修改该配置并重启后端。

## 检查和构建

```powershell
npm run check
npm run build
# 仓库根目录统一检查，-Build 同时构建管理端、小程序和 H5。
..\scripts\check.ps1 -Build
```

生产产物输出到 `admin/dist`，由独立 Nginx 容器提供页面。开发服务器不用于正式部署。服务端接口和发布方法见 [Web 管理端部署](../backend/docs/web-admin.md)。
