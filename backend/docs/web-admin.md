# Web 管理端第一版

## 架构与访问地址

```text
https://mjzs.zf-yun.cn
  ├─ /、/login、/users、/books → 公共 Nginx → moji-admin:80（Vue 3 静态产物）
  ├─ /prod-api/*              → 公共 Nginx → moji-backend:8000（管理 API）
  └─ /api/v1/*                → 公共 Nginx → moji-backend:8000（原小程序 API）
```

前后端是独立项目和独立镜像，通过 HTTP API 通信，同域反向代理避免跨域。后端不提供管理页面，不把 Vue 打包进 Python 镜像。小程序配置及已发布版本不需要因本次管理端发布而修改。

根目录 Compose 的 `IMAGE_TAG` 指定后端版本，`ADMIN_IMAGE_TAG` 可单独指定 Web 版本，未设置时沿用 `IMAGE_TAG`。仅发布样式时只构建和更新 admin 服务，保留后端容器。

管理数据复用现有表，本版无数据库迁移。管理员是后端环境配置中的独立账号，与微信用户表、共享账本持有人无关；本版不包含用户停用、账单改写、多管理员角色、AI 用量监控或完整管理审计中心。

## 登录与数据口径

- `ADMIN_USERNAME`：管理员账号，默认 `admin`；`ADMIN_PASSWORD_HASH` 为 Argon2 摘要，未配置则不能登录。
- 运行 `python admin_password.py` 交互生成密码摘要，生成器要求 12–256 字符密码。正式环境设置 `ADMIN_ORIGIN=https://mjzs.zf-yun.cn`。
- Cookie 为 HttpOnly、SameSite=Strict、Path=/prod-api，正式环境启用 Secure。凭证不进入前端存储或 JSON 响应；Redis 只保存随机凭证的 SHA-256 摘要。
- 管理会话使用 `moji:<环境>:admin:` 命名空间，默认有效八小时，登录按 IP 每分钟最多五次。退出即撤销当前会话；修改账号或密码摘要并重建后端后，旧会话失效。
- 登录与退出校验 Origin 和 Sec-Fetch-Site；浏览器页面与 API 同源访问。命令行请求允许不携带 Origin，但仍需合法凭证。
- 所有管理响应设置 `Cache-Control: no-store`，参数错误不回显密码。没有公开的管理员注册接口。
- 所有日期按北京时间展示；近七天登录用户按 `last_login_at` 统计，并非按每次业务请求统计。有效账单排除软删除记录，账本总数仅统计 `active`，趋势按创建时间归属日期，补记历史账单仍计入实际创建日。
- 用户持有账本数仅统计当前拥有的使用中账本，不等于其参与的所有共享账本。共享成员数量仅统计 active，详情保留退出和移出成员记录。
- 管理端只返回用户 UUID、时间、数量、账本名称和成员身份状态，不返回 OpenID、账单正文、金额、预算、邀请密钥和聊天内容。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/prod-api/auth/login` | JSON 账号密码登录，写入 Cookie |
| GET | `/prod-api/auth/me` | 获取当前管理员，未登录返回 401 |
| POST | `/prod-api/auth/logout` | 撤销会话并清除 Cookie |
| GET | `/prod-api/overview` | 汇总与近十四天趋势 |
| GET | `/prod-api/users` | q、page、page_size 分页查询 |
| GET | `/prod-api/users/{id}` | 用户详情 |
| GET | `/prod-api/books` | q、kind、status、owner_id、page、page_size |
| GET | `/prod-api/books/{id}` | 账本详情和成员记录 |

每页默认 20、最多 100。搜索中的 `%` 与 `_` 按字面处理，不能展开为通配符；ID 搜索不区分大小写。数据接口均要求独立管理员 Cookie，微信 Bearer token 无效。业务写入方法未注册。

## 发布

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

本机先执行 `scripts/check.ps1 -Build` 和真实开发库管理查询集成测试，然后执行：

```powershell
.\scripts\package-release.ps1
```

打包脚本仅包含清单中的源码和静态产物，不包含真实环境文件、初始账号文件、虚拟环境或 node_modules。上传生成的包到新的发布目录，解压后将共享环境文件链接为 `backend/.env.production`，在新目录根部设置 `.env` 的 IMAGE_TAG。

发布前备份当前版本链接、共享环境文件、公共 Nginx 配置及正式数据库。环境文件增加管理员配置；Argon2 摘要必须用单引号包裹，避免 Compose 插值。然后在新版本根目录运行：

```bash
docker compose config --quiet
docker compose build
docker compose up -d --wait --wait-timeout 90
```

本版没有新迁移，无需修改数据库结构。双容器健康后，将 `backend/deploy/nginx.conf` 更新到 `/opt/nginx/conf.d/mjzs.zf-yun.cn.conf`，依次执行 `docker exec nginx-web nginx -t` 和 `docker exec nginx-web nginx -s reload`，随后验证：

1. `/`、`/login`、刷新 `/users` 都返回管理页面。
2. 未登录 `/prod-api/auth/me` 返回 401；登录后概览、用户、账本接口返回真实数据。
3. 登录后刷新可恢复会话，退出后旧会话返回 401。
4. `/api/v1/health` 返回 `{"status":"ok"}`，原小程序接口继续可用。
5. `/prod-api/unknown` 返回 API 404，不能降级为前端 HTML。

全部通过后将 `/opt/keeping-accounts/current` 指向新目录。新版本运维命令从该目录根部运行，后端检查命令仍为 `docker compose exec -T backend ...`。

若发布失败，恢复备份的公共 Nginx 配置和共享环境文件，在旧版本目录运行其 `docker compose up -d --wait`，恢复 current 链接。旧版本没有 admin 服务时可停止新 admin 容器，不删除数据库卷。此次无需数据库降级。

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
