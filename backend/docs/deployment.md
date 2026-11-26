# z236 正式部署

正式服务端和 Web 管理端部署在 `z236`（`60.205.105.236`）。公网入口为 `https://mjzs.zf-yun.cn`，根路径提供管理页面，管理 API 使用 `/prod-api`，小程序 API 使用 `/api/v1`。

当前 `current` 指向 `/opt/keeping-accounts/releases/20261116T073206Z`。后端镜像为 `moji-backend:20261116T073206Z`，Web 镜像为 `moji-admin:20261106T121956Z`，正式库已迁移至 `0011_savings_assistant`。迁移前的发布记录见[后端发布](releases/20261116T073206Z.md)和 [Web 发布](releases/20261106T121956Z.md)。

## 服务器目录与运行方式

Compose 项目名为 `moji`。后端、Web、`postgres16`、`redis6` 和公共 Nginx `nginx-web` 接入同一个 Docker 网络 `moji_net`。公共 Nginx 通过 `moji-backend:8000` 和 `moji-admin:80` 转发请求，应用容器无需发布主机端口。

正式后端使用 PostgreSQL 数据库 `keeping_accounts_prod`、Redis 逻辑库 2 和 `moji:prod:` 前缀。`FORWARDED_ALLOW_IPS` 当前为 `172.30.236.2`，即公共 Nginx 在 `moji_net` 上的地址。Nginx 容器 IP 变化时需同步修改该值并重建后端容器。

当前版本由 `/opt/keeping-accounts/current/docker-compose.yml` 直接定义镜像和环境变量。该文件包含生产凭据，权限为 `600`。从下次源码发布起，按下述步骤恢复共享 `.env.production` 和版本目录结构。

## 常用运维命令

```powershell
ssh z236
```

进入 z236 后执行：

```bash
cd /opt/keeping-accounts/current
docker compose ps
docker compose logs --tail=100 backend
docker compose exec -T backend python check_connections.py
docker compose exec -T backend python -m alembic current
curl --fail https://mjzs.zf-yun.cn/api/v1/health
```

HTTP 健康检查返回 `{"status":"ok"}`。`check_connections.py` 另外检查 PostgreSQL 和 Redis，Redis 探测键会在验证后清理。

## 发布服务端和 Web 管理端

沿用原发布流程。在 Windows 构建 Web 静态文件，将它和后端源码打成部署包，上传到 z236，由 z236 执行 `docker compose build` 构建两个镜像。小程序单独发布。

首次在 z236 执行源码发布前，在服务器上创建 `/opt/keeping-accounts/shared`（权限 `700`）。参照[环境配置示例](../.env.production.example)，把当前后端实际生效的应用环境变量保存在 `/opt/keeping-accounts/shared/.env.production`（权限 `600`），包括既有数据库、Redis、微信、模型、邀请和管理员配置。`ADMIN_ORIGIN` 保持 `https://mjzs.zf-yun.cn`，`FORWARDED_ALLOW_IPS` 使用上述 z236 容器地址。含 `$` 的值用单引号包裹。该文件只在服务器上维护。

在本地仓库根目录执行统一检查后，使用 PowerShell 打包、上传：

```powershell
.\scripts\check.ps1 -Build
$release = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$previousFeatures = $env:VITE_ADMIN_ACCOUNT_FEATURES
$env:VITE_ADMIN_ACCOUNT_FEATURES = 'true'
try { .\scripts\package-release.ps1 -Release $release }
finally { $env:VITE_ADMIN_ACCOUNT_FEATURES = $previousFeatures }
$releaseDir = "/opt/keeping-accounts/releases/$release"
ssh z236 "umask 077; mkdir '$releaseDir'"
if ($LASTEXITCODE -ne 0) { throw '创建远端版本目录失败。' }
scp "output/moji-$release.tar.gz" "output/moji-$release.tar.gz.sha256" "z236:$releaseDir/"
if ($LASTEXITCODE -ne 0) { throw '上传部署包失败。' }
$release
ssh z236
```

在 z236 上将 `release` 设为上一步输出的版本号。发布包自带仓库的 Compose 配置，通过同目录的 override 把 `app_net` 映射到 z236 的 `moji_net`：

```bash
set -euo pipefail
umask 077
release='<本次版本号>'
base=/opt/keeping-accounts
previous=$(readlink -f "$base/current")
cd "$base/releases/$release"
sha256sum --check "moji-$release.tar.gz.sha256"
tar -xzf "moji-$release.tar.gz"
test -s "$base/shared/.env.production"
ln -s "$base/shared/.env.production" backend/.env.production
printf 'IMAGE_TAG=%s\nADMIN_IMAGE_TAG=%s\n' "$release" "$release" > .env
cat > docker-compose.override.yml <<'YAML'
networks:
  app_net:
    name: moji_net
YAML
docker compose config --quiet
docker network inspect moji_net >/dev/null
docker compose build
```

核对新版本的迁移链和回退兼容性。备份生产数据库及配置，再迁移并启动新容器：

```bash
backup="$base/shared/backups/$release"
mkdir -p "$base/shared/backups"
mkdir "$backup"
printf '%s\n' "$previous" > "$backup/previous-release"
install -m 600 "$previous/docker-compose.yml" "$backup/previous-compose.yml"
install -m 600 "$base/shared/.env.production" "$backup/env.production"
install -m 600 /opt/nginx/conf.d/mjzs.zf-yun.cn.conf "$backup/nginx.conf"
docker exec postgres16 sh -c 'pg_dump -U "${POSTGRES_USER:-postgres}" -Fc keeping_accounts_prod' > "$backup/database.dump"
docker exec -i postgres16 pg_restore --list < "$backup/database.dump" > "$backup/database-list.txt"
docker compose run --rm --no-deps -T backend python -m alembic upgrade head
docker compose up -d --no-build --wait --wait-timeout 120
docker compose exec -T backend python check_connections.py
docker compose exec -T backend python -m alembic current
curl --fail --silent --show-error https://mjzs.zf-yun.cn/api/v1/health
curl --fail --silent --show-error https://mjzs.zf-yun.cn/ | grep -F 'id="app"'
curl --fail --silent --show-error https://mjzs.zf-yun.cn/users | grep -F 'id="app"'
test "$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' https://mjzs.zf-yun.cn/prod-api/auth/me)" = 401
```

确认健康检查返回 `{"status":"ok"}`，并按 [Web 验收步骤](web-admin.md#发布)完成验证后，将 `current` 切到新目录：

```bash
ln -sfn "$base/releases/$release" "$base/current"
```

如需回退，进入 `previous-release` 记录的目录，执行 `docker compose up -d --no-build --wait --wait-timeout 120`，复核公网后把 `current` 指回该目录。数据库迁移是否需要回退应单独判断。仓库中的 `deploy-release.sh` 和 `update-release.sh` 对应旧版初始化与指定版本迁移。

## 公共 Nginx 与 HTTPS

站点配置位于 `/opt/nginx/conf.d/mjzs.zf-yun.cn.conf`，证书和私钥位于 `/opt/nginx/cert/mjzs.zf-yun.cn/`。当前证书有效期至 **2027-01-22 06:59:59（北京时间）**。发布源码和镜像时沿用现网 Nginx 配置。确需改动代理或更新证书时，备份现网文件并校验后平滑重载：

```bash
docker exec nginx-web nginx -t
docker exec nginx-web nginx -s reload
curl --fail https://mjzs.zf-yun.cn/api/v1/health
```

## 小程序接入

微信公众平台的 request 合法域名为 `https://mjzs.zf-yun.cn`。体验版和正式版通过该域名访问 `/api/v1`，真机开发地址在 [`frontend/common/config.ts`](../../frontend/common/config.ts) 中配置。小程序发布和真机验收仍在微信开发者工具中单独进行。
