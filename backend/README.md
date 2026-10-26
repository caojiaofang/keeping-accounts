# 墨记后端

基于 **Python 3.12 + FastAPI + SQLAlchemy 2 + PostgreSQL + Redis** 的后端基础框架，使用 Alembic 管理数据库迁移，在 Windows + PyCharm 中开发。

当前已实现微信登录、个人及共享账本、账单新增/编辑/删除、预算、邀请与成员管理、退出/转让/解散及操作记录。开发库与正式库均为 `0004_shared`，正式环境已独立配置 `BOOK_INVITE_SECRET`，详见[共享账本说明](docs/shared-books.md)。sub2api 使用 `gpt-6-luna`，推理强度固定为 `xhigh`；真实聊天分析、流式回答及历史会话已接入，并遵循当前成员权限，详见[聊天消费分析](docs/chat-analysis.md)。自定义分类及独立统计 API 待开发。正式环境版本为 `20261026T050441Z`，详情见[上线记录](docs/releases/20261026T050441Z.md)。

正式服务使用 Docker Compose 部署到 z11，通过 `https://mjzs.zf-yun.cn` 访问，复用公共 Nginx 处理 HTTPS。部署配置、更新和运维方法见[部署说明](docs/deployment.md)。

Web 管理端第一版提供独立管理员登录、平台概览、用户与账本只读查询，管理 API 前缀为 `/prod-api`。Vue 3 + Element Plus 前端位于仓库 `admin/`，与后端分别构建和部署，不影响小程序 `/api/v1`。管理员初始化、接口与双服务发布见 [Web 管理端说明](docs/web-admin.md)。

## 在 PyCharm 中启动

1. 使用 PyCharm 打开仓库根目录，或单独打开 `backend` 目录。
2. 在 Python Interpreter 设置中选择已有解释器 `backend/.venv/Scripts/python.exe`。当前工作区已创建此 Python 3.12 虚拟环境；其他机器请先按下面的命令创建。如果打开的是仓库根目录，可右键 `backend` → **Mark Directory as → Sources Root**，让编辑器正确识别 `app` 导入。
3. 在 PyCharm Terminal 中进入 `backend`，安装开发依赖并复制配置文件。

   ```powershell
   # 已在 backend 目录时无需执行。
   cd backend
   # 当前工作区已创建虚拟环境；其他机器首次配置时执行。
   py -3.12 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
   # 仅在 .env 尚不存在时复制，避免覆盖已有配置。
   if (-not (Test-Path .env)) { Copy-Item .env.example .env }
   ```

4. 右键 `run.py` 选择 **Run** 启动，或选择 **Debug** 调试，无需 FastAPI 专用运行模板。本机 `.env` 已配置远程直连并启用启动连接检查，成功时日志显示 PostgreSQL 已连接到 `keeping_accounts_dev` 且 Redis 已连接。
5. 打开 [Swagger 文档](http://127.0.0.1:8000/docs) 或 [健康检查](http://127.0.0.1:8000/api/v1/health)。健康检查返回 `{"status":"ok"}`。

如果手动创建 Python Run/Debug Configuration，Script path 选择 `backend/run.py`，Working directory 选择 `backend`，解释器选择上述虚拟环境。配置文件通过绝对路径定位，无论打开整个仓库还是后端目录，均读取 `backend/.env`。

`.env.example` 默认 `RELOAD=false`，方便 PyCharm 断点调试；需要修改代码后自动重启时改为 `true`。也可从 `backend` 目录执行 `.\.venv\Scripts\python.exe run.py`。上述命令直接使用虚拟环境解释器，无需激活脚本或修改 PowerShell 执行策略。

如果官方依赖源下载较慢，可临时指定镜像安装，不修改全局 pip 配置：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -i https://mirrors.aliyun.com/pypi/simple/
```

## 目录与职责

```text
backend/
├── app/
│   ├── api/               # 路由、公共依赖，v1 提供健康检查、登录、个人及共享记账和助手
│   ├── core/              # 环境配置、应用生命周期
│   ├── db/                # SQLAlchemy 模型基类
│   ├── models/            # 用户、账本、账单、成员、邀请、操作记录和会话
│   ├── schemas/           # Pydantic 请求和响应结构
│   ├── repositories/      # 用户数据访问、并发登录去重
│   ├── services/          # 登录、个人及共享记账、助手及 Redis 登录态管理
│   ├── integrations/      # 微信 code2Session 与 sub2api 模型适配
│   └── main.py            # 应用工厂 create_app
├── alembic/               # 迁移环境，开发库已到 0004_shared
├── tests/                 # 无需数据库的框架检查
├── .env.example           # 可提交的配置示例
├── alembic.ini
├── pyproject.toml         # pytest、Ruff 配置
├── requirements.txt       # 运行依赖
├── requirements-dev.txt   # 开发与检查依赖
├── check_connections.py    # 真实 PostgreSQL / Redis 连接检查
└── run.py                 # PyCharm 运行和调试入口
```

后续扩展遵循 `api → services → repositories → models` 的职责划分，输入输出结构放入 `schemas`，目前仅预留模块边界。

## 配置说明

配置通过 `pydantic-settings` 读取，系统环境变量优先于 `backend/.env`，最后使用代码默认值。文件采用 UTF-8，真实 `.env` 已被仓库忽略。

| 配置 | 默认值 / 用途 |
| --- | --- |
| `APP_NAME` | `墨记后端` |
| `APP_ENV` | `development`，可选 `test`、`production` |
| `HOST` / `PORT` | `127.0.0.1` / `8000`，用于 `run.py` |
| `RELOAD` | `false`，用于 `run.py` 的开发自动重启 |
| `LOG_LEVEL` | `INFO` |
| `CORS_ORIGINS` | `[]`，浏览器跨域白名单，例如 `["http://localhost:5173"]` |
| `DATABASE_URL` | 可留空，PostgreSQL 的 asyncpg 连接地址 |
| `REDIS_URL` | 可留空，Redis 连接地址 |
| `REDIS_KEY_PREFIX` | `moji:dev:`，为后续 Redis key 命名预留 |
| `CHECK_CONNECTIONS_ON_STARTUP` | 默认 `false`；本机 `.env` 为 `true`，启动时检查数据库及 Redis |
| `WECHAT_APP_ID` / `WECHAT_APP_SECRET` | 小程序 AppID 与后端专用密钥，真实密钥仅填本地 `.env` |
| `WECHAT_TIMEOUT_SECONDS` | 微信请求超时，默认 10 秒 |
| `AUTH_SESSION_TTL_SECONDS` | 登录态有效期，默认 604800 秒（7 天） |
| `AUTH_LOGIN_RATE_LIMIT` | 每个请求来源 IP 每分钟登录次数，默认 30 |
| `BOOK_INVITE_SECRET` | 邀请专用密钥，至少 32 字符；开发与正式分别生成，轮换后旧邀请失效 |
| `LLM_BASE_URL` / `LLM_API_KEY` | sub2api HTTPS 地址及后端专用密钥 |
| `LLM_MODEL` / `LLM_API_MODE` | 默认 `gpt-6-luna` / `responses`；推理强度固定为 `xhigh` |
| `LLM_TIMEOUT_SECONDS` | 单次模型请求超时，默认 45 秒 |
| `CHAT_TIMEOUT_SECONDS` | 流式聊天整轮模型与统计预算，默认 240 秒，范围 10–240 秒；解释超时保留统计 |

未配置连接且保持 `CHECK_CONNECTIONS_ON_STARTUP=false` 时仍可独立启动。设为 `true` 后必须同时配置两项连接，启动时执行 PostgreSQL 只读查询和 Redis PING，检查失败则退出；不会自动建表或迁移。健康检查仅表示应用运行，不持续探测外部服务。服务退出时释放连接资源。

配置校验限制 `development` / `test` 环境只连接 `keeping_accounts_dev` 并使用 `moji:dev:` 前缀；`production` 环境要求 `keeping_accounts_prod` 和 `moji:prod:`。这些是本应用的配置校验规则。

公共依赖位于 `app/api/deps.py`。后续接口使用 `get_db` 获取请求级会话，由业务服务显式提交或使用 `session.begin()` 管理事务，依赖层不自动提交；未配置对应连接时返回 HTTP 503。`get_redis` 提供客户端，后续构造 key 时需要显式使用配置中的前缀，登录态、限流和智能识别已按用途写入带前缀及 TTL 的 key。

日志使用 Python logging 和 Uvicorn 日志，不输出连接 URL 或密钥。Swagger、ReDoc 和 OpenAPI 当前启用，主要用于开发联调。

## PostgreSQL、Redis 与迁移

复用 z11 的现有 `postgres16` 和 `redis6` 容器，不额外部署或重启数据库服务。本机连接配置已于 2026-10-24 写入 `backend/.env`，真实密码不写入示例或提交到 Git。

- PostgreSQL：直连 `47.251.103.11:15432`，数据库为 `keeping_accounts_dev`，专用账号为 `keeping_accounts_dev_app`。账号不是超级用户，已获得开发库连接及 `public` schema 的使用和建表权限，供后续 Alembic 迁移使用。
- Redis：直连 `47.251.103.11:16379`，复用现有密码认证，开发使用逻辑库 **1** 和 `moji:dev:` 前缀。前缀是应用命名约定；当前使用现有 Redis 默认账号，不是 Redis ACL 权限隔离。
- 本地调试直接使用上述远程地址，无需 SSH 隧道。当前机器已完成配置，直接运行 `run.py`；其他机器需单独获取认证信息，示例文件不含真实密码。

| 环境 | PostgreSQL 数据库 | Redis key 前缀 |
| --- | --- | --- |
| 开发 | `keeping_accounts_dev` | `moji:dev:` |
| 正式 | `keeping_accounts_prod` | `moji:prod:` |

```dotenv
DATABASE_URL=postgresql+asyncpg://keeping_accounts_dev_app:<encoded-password>@47.251.103.11:15432/keeping_accounts_dev
REDIS_URL=redis://:<encoded-password>@47.251.103.11:16379/1
REDIS_KEY_PREFIX=moji:dev:
CHECK_CONNECTIONS_ON_STARTUP=true
```

连接 URL 中用户名和密码的特殊字符需要进行 URL 编码。正式环境同时配置 `APP_ENV=production`、正式库及 `REDIS_KEY_PREFIX=moji:prod:`，`APP_ENV` 本身不会切换数据库或 Redis 前缀。正式账号与部署配置见[部署说明](docs/deployment.md)。

从 `backend` 目录验证真实连接，也可在 PyCharm 中直接运行 `check_connections.py`：

```powershell
.\.venv\Scripts\python.exe check_connections.py
```

此命令只读查询 PostgreSQL 的当前数据库和账号，执行 Redis PING，并在当前前缀下创建一个 TTL 为 30 秒的随机探测 key，验证读写后立即删除。不会创建 PostgreSQL 业务表或读取现有业务数据。

当前开发库及正式库均已执行 `0004_shared`，在用户、账本、账单及会话表基础上新增 `book_members`、`book_invitations`、`book_activities`，补充账单修改人和时间。其他开发机器使用同一库时无需重复建表，`alembic upgrade head` 可重复执行。后续添加模型后，在 `app/models/__init__.py` 中导入对应模型，再从 `backend` 目录生成并审核迁移：

```powershell
.\.venv\Scripts\python.exe -m alembic revision --autogenerate -m "新增业务表"
# 先检查生成的迁移脚本，再应用到配置中的开发库。
.\.venv\Scripts\python.exe -m alembic upgrade head
```

迁移需要可访问的 PostgreSQL 和足够的 schema 权限。开发与测试使用开发环境，正式迁移通过后续部署流程执行。框架不会调用 `create_all()` 或在启动时自动执行迁移。

## 开发检查

前后端统一入口：在仓库根目录执行 `.\scripts\check.ps1`，可一并完成前端检查及以下后端检查；追加 `-Build` 会验证微信小程序与 H5 构建。任一检查失败立即停止并返回非零退出码。统一入口临时关闭真实集成及模型测试开关，结束后恢复原值；前置依赖说明见[项目说明](../README.md#开发与检查)。

从 `backend` 目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m pip check
```

默认自动化检查验证框架、微信适配、登录接口、登录态及故障行为，不访问 z11。真实 PostgreSQL / Redis 登录流程测试需显式启用，方法见[微信登录验证说明](docs/wechat-login.md)。依赖文件固定直接依赖的版本，传递依赖由 pip 按兼容约束解析。

框架使用方式参考 [FastAPI 配置管理](https://fastapi.tiangolo.com/advanced/settings/)、[FastAPI 生命周期](https://fastapi.tiangolo.com/advanced/events/)、[Alembic 异步迁移示例](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic)及 [Redis 异步客户端](https://redis.io/docs/latest/develop/clients/redis-py/async/)。业务规划见[项目方案说明](../项目方案说明.md)。
