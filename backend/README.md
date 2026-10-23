# 墨记后端

后端技术选型已确定为 **Python + FastAPI + PostgreSQL + Redis**。当前尚未初始化后端应用，本文件用于保留后端目录并说明后续开发范围。

## 计划实现

- 微信登录、用户身份与账本权限校验。
- 账本、分类、收支账单、预算和统计 API。
- PostgreSQL 持久化与数据库迁移。
- Redis 登录态、接口限流与短期防重复提交。
- 通过配置的 sub2api 中转站调用大模型，生成一句话记账草稿。
- 聊天会话、受控账单查询工具和消费分析。
- 模型结果校验、确认保存、重复提交保护、超时及调用次数限制。

数据访问建议使用 SQLAlchemy，数据库迁移建议使用 Alembic，数据校验使用 Pydantic。具体目录结构和依赖版本在后端开发时确定。

## 复用 z11 的数据库服务

PostgreSQL 和 Redis 均复用 z11 上现有的服务，不额外部署新的数据库实例。

- PostgreSQL：开发库为 `keeping_accounts_dev`，正式库为 `keeping_accounts_prod`，保存用户、账本、账单、预算、聊天会话和消息。建表及表结构变更通过 Alembic 管理，迁移账号需要具有目标库及目标 schema 的相应权限。
- Redis：保存有过期时间的登录态、限流计数与短期幂等状态，开发环境 key 前缀为 `moji:dev:`，正式环境为 `moji:prod:`，避免混用临时状态。实际逻辑库编号在核对现有部署后配置。
- 账单和长期聊天记录以 PostgreSQL 为准；Redis 中的数据过期或被清理后，不应导致正式账单或历史消息丢失。账单写入还需通过 PostgreSQL 事务和唯一约束保证一致性，不能只靠 Redis 防重。

建议后端使用 SQLAlchemy 的异步数据库访问，PostgreSQL 驱动选择 `asyncpg`，Redis 使用支持异步调用的客户端。SQLAlchemy 提供了对应的 [asyncpg 接入方式](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#module-sqlalchemy.dialects.postgresql.asyncpg)。

两个数据库已于 2026-10-23 在 z11 的 `postgres16` 容器（PostgreSQL 16.14）中创建，均为 UTF-8 编码，所有者为现有的 `postgres` 角色。当前为空库，后续通过数据库迁移创建业务表，应用连接账号另行配置。

| 环境 | 数据库 | Redis key 前缀 |
| --- | --- | --- |
| 开发 | `keeping_accounts_dev` | `moji:dev:` |
| 正式 | `keeping_accounts_prod` | `moji:prod:` |

后续通过环境变量提供连接配置，以下为开发环境的配置格式：

```dotenv
DATABASE_URL=postgresql+asyncpg://<user>:<password>@<host>:<port>/keeping_accounts_dev
REDIS_URL=redis://<user>:<password>@<host>:<port>/<db>
REDIS_KEY_PREFIX=moji:dev:
```

正式环境使用 `keeping_accounts_prod`，并设置 `REDIS_KEY_PREFIX=moji:prod:`。实际主机、端口、认证和 TLS 配置以 z11 的部署为准，连接 URL 中的特殊字符需要正确编码。

数据库迁移先在开发库验证，再由正式环境的部署流程执行；开发运行与自动化测试使用开发库，不连接正式库。Redis 前缀目前仅为配置约定，尚未写入服务配置或创建任何 Redis key。

详细方案见[项目方案说明](../项目方案说明.md)。模型密钥、微信 AppSecret 和数据库密码仅通过本地或服务器环境配置，不能写入前端或提交到 Git。
