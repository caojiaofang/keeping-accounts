# z11 正式部署

后端通过 Docker Compose 运行，公网地址为 `https://mjzs.zf-yun.cn`。根路径跳转到 `/docs`；健康检查为 `/api/v1/health`，接口前缀为 `/api/v1`。

## 服务器目录与运行方式

```text
/opt/keeping-accounts/
├── current -> releases/<版本>     # 当前上线版本
├── releases/<版本>/              # 后端源码、Dockerfile、docker-compose.yml
│   ├── .env                      # IMAGE_TAG，固定该版本的镜像标签
│   └── .env.production -> /opt/keeping-accounts/shared/.env.production
└── shared/
    ├── .env.production           # 正式凭据，仅 root 可读
    └── tls/                      # 证书、私钥的原始副本

/opt/nginx/conf.d/mjzs.zf-yun.cn.conf
/opt/nginx/cert/mjzs.zf-yun.cn/fullchain.pem
/opt/nginx/cert/mjzs.zf-yun.cn/privkey.pem
```

Compose 项目名为 `moji`，服务名为 `backend`，通过已有 `app_net` 网络连接 `postgres16:5432`、`redis6:6379` 和 `nginx-web`，不发布额外的主机端口。Nginx 的配置源文件为 `backend/deploy/nginx.conf`，通过 Docker DNS 定期解析 `moji-backend`，容器更新后不需要为地址变化重载 Nginx。

正式环境使用数据库 `keeping_accounts_prod`、专用非超级用户 `keeping_accounts_prod_app`，Redis 使用逻辑库 **2** 与 `moji:prod:` 前缀。开发库、开发账号和 Redis 逻辑库 1 继续供本地开发使用。Redis 复用已有认证，逻辑库与前缀不构成 ACL 权限隔离。

实际凭据仅保存在服务器 `shared/.env.production`，权限为 `600`。应用镜像不包含 `.env`、证书、私钥和本地虚拟环境，使用 UID `10001` 运行，并启用只读根文件系统和日志轮转。

`FORWARDED_ALLOW_IPS` 只信任现有 Nginx 的容器 IP。Nginx 容器重建后，如 IP 发生变化，应同步修改正式环境文件并使用 `docker compose up -d --force-recreate backend` 重建后端。

## 常用运维命令

```powershell
ssh z11
```

进入服务器后执行：

```bash
cd /opt/keeping-accounts/current
docker compose ps
docker compose logs --tail=100 backend
docker compose exec -T backend python check_connections.py
docker compose exec -T backend python -m alembic current
curl --fail https://mjzs.zf-yun.cn/api/v1/health
```

健康检查返回 `{"status":"ok"}`。容器启动时检查 PostgreSQL 和 Redis，检查失败会退出；运行期间的 HTTP 健康检查仅确认应用可响应。`check_connections.py` 还会读写一个短期 Redis 探测 key 并立即清理。

## 更新后端

先在本机执行现有 pytest、Ruff 检查。每次更新创建新版本目录并保留旧镜像，避免覆盖正在运行的源码。以下在仓库根目录的 PowerShell 执行，仅打包部署所需文件：

```powershell
$release = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
New-Item -ItemType Directory -Force output | Out-Null
tar --exclude=__pycache__ --exclude=*.pyc -czf output/backend.tar.gz -C backend app alembic alembic.ini check_connections.py requirements.txt Dockerfile .dockerignore docker-compose.yml .env.production.example deploy docs README.md
$release
ssh z11 "mkdir -p /opt/keeping-accounts/releases/$release"
scp output/backend.tar.gz "z11:/opt/keeping-accounts/releases/$release/backend.tar.gz"
```

随后 SSH 登录，设置 `release` 为上一步显示的版本号，再执行：

```bash
set -eu
release=<版本号>
cd /opt/keeping-accounts/releases/"$release"
tar -xzf backend.tar.gz
rm backend.tar.gz
ln -s /opt/keeping-accounts/shared/.env.production .env.production
printf 'IMAGE_TAG=%s\n' "$release" > .env
docker compose config --quiet
docker compose build --pull
docker compose run --rm --no-deps -T backend python -m alembic upgrade head </dev/null
docker compose up -d --wait --wait-timeout 90
curl --fail https://mjzs.zf-yun.cn/api/v1/health
ln -sfn /opt/keeping-accounts/releases/"$release" /opt/keeping-accounts/current
```

Compose 中的重启策略会在 Docker 重启后恢复服务。数据库迁移在启动服务前显式执行，应用启动本身不建表。涉及已有数据的迁移应先备份正式库并评估旧版本兼容性；不要通过删除数据库卷来更新应用。

如新版本异常，进入上一版本目录，执行 `docker compose up -d --wait` 并把 `current` 链接指回上一版本。数据库迁移是否需要回退应单独判断，避免盲目执行 `alembic downgrade`。首次部署没有可回退的旧应用版本。

## HTTPS 证书

当前使用用户提供的三段完整证书链及配套 ECC 私钥，证书域名为 `mjzs.zf-yun.cn`。有效期截止 **2027-01-22 06:59:59（北京时间）**，本部署不自动续期。

续期时替换 `/opt/nginx/cert/mjzs.zf-yun.cn/` 中的证书和私钥，同步更新 `shared/tls/` 副本。私钥保持 `600`，然后校验并平滑重载：

```bash
docker exec nginx-web nginx -t
docker exec nginx-web nginx -s reload
curl --fail https://mjzs.zf-yun.cn/api/v1/health
```

## 小程序接入

将 `https://mjzs.zf-yun.cn` 添加到微信公众平台的小程序服务器域名中的 request 合法域名。前端通过 `uni.getAccountInfoSync()` 识别运行版本，仅 `release` 正式版固定连接该域名；开发版、体验版、未知版本和读取失败时使用本地开发地址，默认 `http://127.0.0.1:8000`。前端无需 `.env`，直接在 `frontend/common/config.ts` 的 `DEV_BASE_URL` 与 `PROD_BASE_URL` 中调整地址并重新编译。真实微信登录仍需小程序调用 `wx.login` 获取一次性 code，无法用固定 code 完成真实登录验证。

本次仅部署当前已有的健康检查和微信认证接口。账本、账单等尚未实现的后端接口不会因为部署而新增。
