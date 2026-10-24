# 微信小程序登录接口

本接口用于微信小程序登录，使用 AppID `wx0f90babab478b2b6`。AppSecret 只保存在后端 `.env`，示例文件不含真实密钥。

小程序调用 `wx.login` 获取临时 code，将 code 发给本后端；后端调用微信 `code2Session`，从可信的微信响应获取 `openid`。首次登录创建用户，再次登录复用同一用户并更新登录时间。用户通过本项目独立签发的随机 Bearer token 访问后续接口。

`openid` 在同一个小程序下唯一，数据库使用 `(wechat_app_id, openid)` 唯一约束，防止并发登录创建重复用户。内部另有 UUID 用户 ID，便于后续关联账本和账单。接口不接受客户端自报 `openid` 作为登录凭据。

## 配置与启动

当前机器的后端 `.env` 已配置真实微信凭据，并已通过微信官方接口校验。开发数据库为 `keeping_accounts_dev`，Redis 使用 z11 的逻辑库 1。

```dotenv
WECHAT_APP_ID=wx0f90babab478b2b6
WECHAT_APP_SECRET=
WECHAT_TIMEOUT_SECONDS=10
AUTH_SESSION_TTL_SECONDS=604800
AUTH_LOGIN_RATE_LIMIT=30
```

上方仅展示配置名称，`WECHAT_APP_SECRET` 在真实 `.env` 中填写；不会在文档、日志或接口响应中展示。默认登录态有效期为 7 天，每个请求来源 IP 每分钟最多发起 30 次登录。登录态有效期与微信 `session_key` 无关。

本机开发库已执行 `0001_users` 迁移。其他开发环境配置数据库后，从 `backend` 执行迁移，再在 PyCharm 中运行 `run.py`：

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe run.py
```

数据库迁移由命令显式执行，应用启动不会自动建表。首次迁移会创建 `users` 和 Alembic 版本记录表。

## 使用 code 登录

`POST /api/v1/auth/wechat/login`

```json
{
  "code": "wx.login 刚返回的一次性 code"
}
```

成功响应 HTTP 200，以下为示例数据：

```json
{
  "access_token": "本项目签发的随机登录凭证",
  "token_type": "bearer",
  "expires_in": 604800,
  "is_new_user": true,
  "user": {
    "id": "8301be9b-2387-47f0-91af-6f6db7a95c5d",
    "openid": "由微信返回的用户唯一标识",
    "created_at": "2026-10-24T00:00:00Z",
    "last_login_at": "2026-10-24T00:00:00Z"
  }
}
```

前端所需的微信唯一标识为 `user.openid`。请求只允许 `code` 字段，不传 AppSecret、openid、session_key、用户 ID 或客户端指定的 AppID。微信会话密钥只用于校验上游响应，本模块不持久化、不下发该密钥；也不会获取手机号、昵称或头像。

code 不能复用。发生过期、已使用或网络超时等情况时，重新调用 `wx.login` 获取新 code，不自动重试同一个 code。

## 获取当前用户

`GET /api/v1/auth/me`

请求头：

```http
Authorization: Bearer <access_token>
```

返回 HTTP 200，响应结构与登录结果中的 `user` 相同。无 token、格式错误、已到期、已注销或用户不存在时返回 HTTP 401。

openid 用于标识用户，不能代替登录凭证。后端先校验 Redis 登录态，再按其中的用户 ID 和当前 AppID 查询数据库，不能通过传入其他人的 openid 获取身份。

## 退出登录

`POST /api/v1/auth/logout`

携带相同的 Bearer 请求头，成功返回 HTTP 204，无响应体。只注销当前 token，其他设备或其他次登录的 token 保持有效；对已注销的合法格式 token 重复调用仍返回 204。

## uni-app 调用示例

前端已通过 `store/auth.ts` 和 `common/api.ts` 接入这些接口，提供登录页、状态恢复和退出登录。以下为接口调用示意，完整实现以实际前端代码为准。`API_BASE_URL` 替换为调试设备可访问的后端地址；默认 `127.0.0.1` 仅本机可访问，真机需另行配置。

```javascript
async function loginWithWeChat() {
  const loginResult = await uni.login({ provider: 'weixin' })
  if (!loginResult.code) throw new Error('微信未返回登录 code，请重试')

  const response = await uni.request({
    url: `${API_BASE_URL}/api/v1/auth/wechat/login`,
    method: 'POST',
    data: { code: loginResult.code },
  })
  if (response.statusCode !== 200) {
    throw new Error(response.data.detail || '登录失败，请重试')
  }
  uni.setStorageSync('access_token', response.data.access_token)
  return response.data.user
}

async function fetchCurrentUser() {
  return uni.request({
    url: `${API_BASE_URL}/api/v1/auth/me`,
    header: {
      Authorization: `Bearer ${uni.getStorageSync('access_token')}`,
    },
  })
}
```

请求业务接口时携带 token；收到 401 后清理旧 token 并重新走微信登录。不要将 token、code 或 AppSecret 输出到前端日志。

## 错误约定

错误体采用 `{"detail":"中文说明"}`，登录凭证和用户信息响应设置 `Cache-Control: no-store`。

| HTTP 状态 | 含义 |
| --- | --- |
| 401 | 微信 code 无效或已使用；或者本项目的登录态无效、到期、被注销 |
| 403 | 微信返回账号风险限制 |
| 422 | 请求缺少 code、code 为空或过长、传入多余字段 |
| 429 | 本项目登录频率限制或微信调用频率限制；带 `Retry-After: 60` |
| 502 | 微信返回异常状态、未知错误或不符合约定的数据 |
| 503 | 微信凭据未配置或无效、微信繁忙、数据库或 Redis 不可用 |
| 504 | 调用微信服务超时 |

Redis 中的登录态 key 使用 `moji:dev:auth:<appid>:session:<token 的 SHA-256 摘要>`，值只保存内部用户 ID，并设置 TTL。登录限流计数通过 Lua 原子增加并设置 60 秒过期时间。不会把明文 token 存入 Redis key 或值中。Redis 数据清理或过期后需要重新登录，不会删除 PostgreSQL 用户记录。

来源 IP 使用服务接收到的 `request.client.host`，应用不自行信任客户端传入的 `X-Forwarded-For`。后续部署反向代理时，应按实际代理配置可信来源。

## 验证

2026-10-24 已在微信开发者工具中编译、运行当前前端，并通过其真实 `wx.login` 和 `wx.request` 完成联调，使用实际微信身份及 z11 开发 PostgreSQL / Redis，未模拟微信响应：

- 登录和当前用户查询返回 200；再次使用新 code 登录返回同一用户 ID 和 openid。
- 重放已使用的 code 返回 401；退出返回 204，退出后的用户查询返回 401。
- 只退出一个 token 时，另一登录 token 仍可查询当前用户。

联调取得的真实用户记录保留在开发库，测试登录凭证已注销。联调脚本和脱敏结果位于被 Git 忽略的 `frontend/output/wechat-debug`。前端现已提供登录页、状态恢复和账号本地缓存隔离；账本与账单 API 仍待实现。

默认自动化测试模拟微信和存储故障，不调用真实微信服务，也不连接开发库：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

显式启用真实 PostgreSQL 和 Redis 测试：

```powershell
$env:MOJI_RUN_INTEGRATION_TESTS = '1'
try {
  .\.venv\Scripts\python.exe -m pytest tests/test_auth_integration.py -q
} finally {
  Remove-Item Env:MOJI_RUN_INTEGRATION_TESTS
}
```

该集成测试只允许开发环境，模拟微信身份，使用外层 PostgreSQL 事务回滚测试用户，Redis 使用随机独立前缀并清理。覆盖首次登录、重复用户、token 有效期、当前用户、单凭证退出、限流和无效 code，不会留下模拟微信用户。

真实微信用户端到端验收需要微信开发者工具或真机 `wx.login` 返回的新 code。可在开发者工具控制台运行 `wx.login({ success: r => console.log(r.code) })`，随后在 Swagger 中调用登录接口；微信身份以官方接口实际返回值为准。

协议依据：[微信小程序登录凭证校验](https://developers.weixin.qq.com/miniprogram/dev/server/API/user-login/api_code2session.html)、[微信小程序登录流程](https://developers.weixin.qq.com/miniprogram/dev/framework/open-ability/login.html)。
