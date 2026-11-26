# Web 管理端

Web 管理端现部署在 z236，镜像版本为 [`20261106T121956Z`](releases/20261106T121956Z.md)。这个版本于 2026-11-06 北京时间 20:23 首次上线，支持在用户列表与详情展示云端昵称，并保留管理员密码、账号设置和登录日志。新版本正式构建仍使用 `VITE_ADMIN_ACCOUNT_FEATURES=true`。

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

本地构建 Web 静态文件，将 `admin/dist` 和后端源码打包上传到 `z236`，再在服务器上运行 `docker compose build`。打包时保留 `VITE_ADMIN_ACCOUNT_FEATURES=true`，已有的管理员账号继续通过正式数据库使用。具体命令见 [z236 正式部署](deployment.md#发布服务端和-web-管理端)。

切换 `current` 前验证以下路径和登录流程：

1. `/`、`/login` 和 `/users` 刷新后均返回管理页面。
2. 未登录时 `/prod-api/auth/me` 返回 401。登录后核对概览、用户和账本页面。
3. 登录后刷新可恢复会话，退出后旧会话返回 401。
4. `/api/v1/health` 返回 `{"status":"ok"}`，小程序继续访问 `/api/v1`。
5. `/prod-api/unknown` 返回 API 404。

首次部署和 `0004_shared` 至 `0006_budget` 的迁移见[历史发布记录](releases/20261106T115227Z.md)。

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
