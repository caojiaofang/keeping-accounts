#!/usr/bin/env bash
# 在 z11 上执行；源码包和仅含密码摘要的初始化 JSON 已提前上传。
set -Eeuo pipefail
release=${1:?需要版本号}
[[ "$release" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || exit 2
base=/opt/keeping-accounts
target="$base/releases/$release"
backup="$base/shared/backups/$release"
previous=$(readlink -f "$base/current")
[[ "$previous" == "$base/releases/"* && -d "$target" && ! -e "$backup" ]] || exit 2
mkdir -m 700 "$backup"
cp -p "$base/shared/.env.production" "$backup/env.production"
cp -p /opt/nginx/conf.d/mjzs.zf-yun.cn.conf "$backup/nginx.conf"
printf '%s\n' "$previous" > "$backup/previous-release"
docker inspect moji-backend-1 --format '{{.Config.Image}}' > "$backup/previous-image"
chmod 600 "$backup/"*
activated=0
rollback() {
    trap - ERR
    echo '发布未完成，正在恢复原版本。' >&2
    cp -p "$backup/env.production" "$base/shared/.env.production"
    cp -p "$backup/nginx.conf" /opt/nginx/conf.d/mjzs.zf-yun.cn.conf
    if [[ "$activated" == 1 ]]; then
        (cd "$target" && docker compose stop admin) || true
        (cd "$previous" && docker compose up -d --no-build --force-recreate --wait --wait-timeout 120) || true
    fi
    docker exec nginx-web nginx -t && docker exec nginx-web nginx -s reload || true
    ln -sfn "$previous" "$base/current"
    exit 1
}
trap rollback ERR

echo '备份正式数据库。'
docker exec postgres16 sh -c 'pg_dump -U "${POSTGRES_USER:-postgres}" -Fc keeping_accounts_prod' > "$backup/database.dump"
docker exec -i postgres16 pg_restore --list < "$backup/database.dump" > "$backup/database-list.txt"
chmod 600 "$backup/"*

cd "$target"
tar -xzf "moji-$release.tar.gz"
ln -s "$base/shared/.env.production" backend/.env.production
printf 'IMAGE_TAG=%s\n' "$release" > .env
chmod 600 admin-setup.json
python3 - "$base/shared/.env.production" admin-setup.json <<'PY'
import json
import os
import re
import sys
from pathlib import Path

env_path, setup_path = map(Path, sys.argv[1:])
values = json.loads(setup_path.read_text(encoding="utf-8"))
allowed = {"ADMIN_USERNAME", "ADMIN_PASSWORD_HASH", "ADMIN_ORIGIN",
           "ADMIN_SESSION_TTL_SECONDS", "ADMIN_LOGIN_RATE_LIMIT"}
assert set(values) == allowed
assert values["ADMIN_ORIGIN"] == "https://mjzs.zf-yun.cn"
assert values["ADMIN_PASSWORD_HASH"].startswith("$argon2id$")
content = env_path.read_text(encoding="utf-8")
for key, value in values.items():
    assert "'" not in value and "\n" not in value
    content = re.sub(rf"^{key}=.*\r?\n?", "", content, flags=re.M)
    content = content.rstrip() + f"\n{key}='{value}'\n"
temp = env_path.with_suffix(".admin-tmp")
temp.write_text(content, encoding="utf-8")
os.chmod(temp, 0o600)
os.replace(temp, env_path)
setup_path.unlink()
PY

docker compose config --quiet
echo '构建前后端独立镜像，日志保存在服务器备份目录。'
docker compose build > "$backup/build.log" 2>&1
activated=1
docker compose up -d --wait --wait-timeout 120 > "$backup/up.log" 2>&1
docker compose exec -T backend python check_connections.py > "$backup/connections-check.log" 2>&1
docker compose exec -T backend python -m alembic current > "$backup/database-version.log" 2>&1

cp backend/deploy/nginx.conf /opt/nginx/conf.d/mjzs.zf-yun.cn.conf
docker exec nginx-web nginx -t
docker exec nginx-web nginx -s reload
echo '验证页面、管理接口鉴权与小程序接口。'
curl --fail --silent --show-error https://mjzs.zf-yun.cn/api/v1/health > "$backup/health.json"
curl --fail --silent --show-error https://mjzs.zf-yun.cn/ > "$backup/index.html"
grep -q 'id="app"' "$backup/index.html"
curl --fail --silent --show-error https://mjzs.zf-yun.cn/users > "$backup/users.html"
code=$(curl --silent --show-error --output "$backup/admin-anonymous.json" --write-out '%{http_code}' https://mjzs.zf-yun.cn/prod-api/auth/me)
[[ "$code" == 401 ]]
code=$(curl --silent --show-error --output "$backup/admin-not-found.json" --write-out '%{http_code}' https://mjzs.zf-yun.cn/prod-api/unknown)
[[ "$code" == 404 ]]
ln -sfn "$target" "$base/current"
docker inspect moji-backend-1 --format '{{.Image}}' > "$backup/deployed-backend-image"
docker inspect moji-admin-1 --format '{{.Image}}' > "$backup/deployed-admin-image"
cp -p "$base/shared/.env.production" "$backup/env.deployed"
chmod 600 "$backup/"*
trap - ERR
echo "发布完成：$release"
docker compose ps
