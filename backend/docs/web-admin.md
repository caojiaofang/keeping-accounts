# Web 管理端

Web 正式页面已于 2026-11-06 北京时间 20:23 更新至 [`20261106T121956Z`](releases/20261106T121956Z.md)。两个服务 healthy，正式库为 `0007_nickname`，用户列表与详情已展示云端昵称。管理员密码、账号设置与登录日志保留，Web 构建开关仍为 `VITE_ADMIN_ACCOUNT_FEATURES=true`；小程序本次未上传。

2026-11-06 北京时间 20:42 [仅后端更新至 `20261106T124007Z`](releases/20261106T124007Z.md)，提供家庭账本账号昵称，Web 镜像与容器保持原样。

## 架构与访问地址

```text
https://mjzs.zf-yun.cn
  ├─ /、/login、/users、/books、/account、/login-logs → 公共 Nginx → moji-admin:80
  ├─ /prod-api/*              → 公共 Nginx → moji-backend:8000（管理 API）
  └─ /api/v1/*                → 公共 Nginx → moji-backend:8000（原小程序 API）
```

前后端是独立项目和独立镜像，通过 HTTP API 通信，同域反向代理避免跨域。后端不提供管理页面，不把 Vue 打包进 Python 镜像。小程序配置及已发布版本不需要因本次管理端发布而修改。

根目录 Compose 的 `IMAGE_TAG` 指定后端版本，`ADMIN_IMAGE_TAG` 可单独指定 Web 版本，未设置时沿用 `IMAGE_TAG`。仅发布样式时只构建和更新 admin 服务，保留后端容器。

业务查询复用现有表；`0005_admin` 新增独立管理员和操作日志表。管理员与微信用户、共享账本持有人无关；不包含用户停用、账单改写、多管理员角色、AI 用量监控或完整管理审计中心。家庭预算 API 服务小程序，后台 Web 本次没有增加预算编辑页面。

## 登录与数据口径

- `ADMIN_USERNAME`、`ADMIN_PASSWORD_HASH` 用于 `python admin_account.py bootstrap` 初始化独立管理员；已有账号不会被覆盖。生产已沿用原摘要初始化，后续以数据库账号为准。
- 运行 `python admin_password.py` 交互生成密码摘要，生成器要求 12–256 字符密码。正式环境设置 `ADMIN_ORIGIN=https://mjzs.zf-yun.cn`。
- Cookie 为 HttpOnly、SameSite=Strict、Path=/prod-api，正式环境启用 Secure。凭证不进入前端存储或 JSON 响应；Redis 只保存随机凭证的 SHA-256 摘要。
- 管理会话使用 `moji:<环境>:admin:v2:` 命名空间，默认有效八小时，登录按 IP 每分钟最多五次。会话绑定数据库账号版本；退出撤销当前会话，修改密码或退出全部会话会递增版本并使旧会话失效。
- 登录与退出校验 Origin 和 Sec-Fetch-Site；浏览器页面与 API 同源访问。命令行请求允许不携带 Origin，但仍需合法凭证。
- 所有管理响应设置 `Cache-Control: no-store`，参数错误不回显密码。没有公开的管理员注册接口。
- 所有日期按北京时间展示；近七天登录用户按 `last_login_at` 统计，并非按每次业务请求统计。有效账单排除软删除记录，账本总数仅统计 `active`，趋势按创建时间归属日期，补记历史账单仍计入实际创建日。
- 用户持有账本数仅统计当前拥有的使用中账本，不等于其参与的所有共享账本。共享成员数量仅统计 active，详情保留退出和移出成员记录。
- 管理端只返回用户 UUID、账号昵称、时间、数量、账本名称和成员身份状态，不返回 OpenID、账单正文、金额、预算、邀请密钥和聊天内容。用户列表与详情中的 `nickname` 来自小程序设置的云端昵称，未设置为 `null`；需先应用 `0007_nickname` 迁移。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/prod-api/auth/login` | JSON 账号密码登录，写入 Cookie |
| GET | `/prod-api/auth/me` | 获取当前管理员，未登录返回 401 |
| POST | `/prod-api/auth/logout` | 撤销会话并清除 Cookie |
| GET | `/prod-api/account` | 获取当前账号设置 |
| POST | `/prod-api/account/password` | 校验原密码、修改密码并撤销旧会话 |
| POST | `/prod-api/account/logout-all` | 撤销全部会话 |
| GET | `/prod-api/account/logs` | 分页查询登录与账号操作日志 |
| GET | `/prod-api/overview` | 汇总与近十四天趋势 |
| GET | `/prod-api/users` | q、page、page_size 分页查询 |
| GET | `/prod-api/users/{id}` | 用户详情 |
| GET | `/prod-api/books` | q、kind、status、owner_id、page、page_size |
| GET | `/prod-api/books/{id}` | 账本详情和成员记录 |

每页默认 20、最多 100。搜索中的 `%` 与 `_` 按字面处理，不能展开为通配符；ID 搜索不区分大小写。数据接口均要求独立管理员 Cookie，微信 Bearer token 无效。业务写入方法未注册。

## 发布

当前生产已发布新管理员 API 与家庭预算 API，账号页面构建时显式设置 `VITE_ADMIN_ACCOUNT_FEATURES=true`，详见[构建开关](../../admin/README.md#账号功能开关)。本次迁移、备份、只读验收、故障恢复及回退命令见[发布记录](releases/20261106T115227Z.md)。`scripts/update-release.sh` 为本次从 `0004_shared` 升级的专用流程，依赖随版本保存的清单与验收脚本，不能直接作为后续版本的通用升级命令。下面保留首次部署流程供历史参考；新版本必须先备份并核对迁移链，再切换应用。

首次从原后端单服务升级为仓库根目录双服务 Compose。旧发布目录保持原样，新的目录结构为：

```text
/opt/keeping-accounts/releases/<新版本>/
  ├─ docker-compose.yml
  ├─ .env                       # IMAGE_TAG
  ├─ backend/
  │   ├─ .env.production -> /opt/keeping-accounts/shared/.env.production
  │   └─ ...
  └─ admin/                     # Dockerfile、deploy/nginx.conf、dist
```

本机先执行 `scripts/check.ps1 -Build` 和隔离 PostgreSQL 管理查询集成测试。当前版本需以 `VITE_ADMIN_ACCOUNT_FEATURES=true` 构建后使用 `-SkipBuild` 保留该产物：

```powershell
$env:VITE_ADMIN_ACCOUNT_FEATURES='true'
npm.cmd --prefix admin run build
Remove-Item Env:VITE_ADMIN_ACCOUNT_FEATURES
.\scripts\package-release.ps1 -SkipBuild
```

打包脚本仅包含清单中的源码和静态产物，不包含真实环境文件、初始账号文件、虚拟环境或 node_modules。上传生成的包到新的发布目录，解压后将共享环境文件链接为 `backend/.env.production`，在新目录根部设置 `.env` 的 IMAGE_TAG。

发布前备份当前版本链接、共享环境文件、公共 Nginx 配置及正式数据库。环境文件增加管理员配置；Argon2 摘要必须用单引号包裹，避免 Compose 插值。然后在新版本根目录运行：

```bash
docker compose config --quiet
docker compose build
docker compose up -d --wait --wait-timeout 90
```

首次静态管理端部署没有新迁移；当前版本须在切换前执行至 `0006_budget`，首次启用账号表再运行 `python admin_account.py bootstrap`。公共 Nginx 已配置，无改动时无需覆盖或重载；配置变化时备份、检查并重载，随后验证：

1. `/`、`/login`、刷新 `/users` 都返回管理页面。
2. 未登录 `/prod-api/auth/me` 返回 401；登录后概览、用户、账本接口返回真实数据。
3. 登录后刷新可恢复会话，退出后旧会话返回 401。
4. `/api/v1/health` 返回 `{"status":"ok"}`，原小程序接口继续可用。
5. `/prod-api/unknown` 返回 API 404，不能降级为前端 HTML。

全部通过后将 `/opt/keeping-accounts/current` 指向新目录。新版本运维命令从该目录根部运行，后端检查命令仍为 `docker compose exec -T backend ...`。

若发布失败，在旧版本目录运行其 `docker compose up -d --no-build --wait`，恢复 current 链接；仅在配置确实变化时恢复配置。旧版本没有 admin 服务时可停止新 admin 容器，不删除数据库卷。当前 `0005_admin`、`0006_budget` 为增量表，回退旧应用时保留结构，不自动降级数据库；详见本次发布记录。

## 验证

```powershell
# 默认检查不连接真实数据库。
.\scripts\check.ps1 -Build
# 显式验证真实开发库，测试数据在事务末尾回滚。
$env:MOJI_RUN_INTEGRATION_TESTS='1'
.\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_admin_integration.py -q
Remove-Item Env:MOJI_RUN_INTEGRATION_TESTS
```

权限与 Cookie 测试位于 `backend/tests/test_admin.py`；真实 PostgreSQL 测试验证创建日北京时间边界、软删除、分页无重复、搜索转义、共享成员和 404。
