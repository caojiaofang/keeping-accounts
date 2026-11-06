#!/usr/bin/env bash
# 本次从 0004_shared 升至 0006_budget 的专用发布流程；依赖版本目录中的验收脚本。
# 先备份和迁移，再切换；不重设账号或修改环境、Nginx。
set -Eeuo pipefail
release=${1:?需要版本号}
[[ "$release" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || exit 2
base=/opt/keeping-accounts
target="$base/releases/$release"
backup="$base/shared/backups/$release"
previous=$(readlink -f "$base/current")
[[ "$previous" == "$base/releases/"* && "$previous" != "$target" ]] || exit 2
[[ -d "$target" && ! -e "$backup" && -f "$target/release-manifest.json" ]] || exit 2
mkdir -m 700 "$backup"
cp -p "$base/shared/.env.production" "$backup/env.production"
cp -p /opt/nginx/conf.d/mjzs.zf-yun.cn.conf "$backup/nginx.conf"
printf '%s\n' "$previous" > "$backup/previous-release"
docker inspect moji-backend-1 --format '{{.Config.Image}} {{.Image}}' > "$backup/previous-backend-image"
docker inspect moji-admin-1 --format '{{.Config.Image}} {{.Image}}' > "$backup/previous-admin-image"
chmod 600 "$backup/"*
activated=0
rollback() {
    trap - ERR
    echo '发布未完成，恢复原应用镜像；保留已成功执行的增量表，不自动降级数据库。' >&2
    if [[ "$activated" == 1 ]]; then
        if (cd "$previous" && docker compose up -d --no-build --force-recreate --wait --wait-timeout 120); then
            ln -sfn "$previous" "$base/current"
        else
            echo '自动恢复应用失败，请按备份中的 previous-release 检查服务。' >&2
        fi
    fi
    exit 1
}
trap rollback ERR

cd "$target"
sha256sum --check archive.sha256
tar -xzf "moji-$release.tar.gz"
python3 - <<'PY'
import hashlib
import json
from pathlib import Path

manifest = json.loads(Path("release-manifest.json").read_text(encoding="utf-8"))
for name, expected in manifest.items():
    path = Path(name)
    assert not path.is_absolute() and ".." not in path.parts
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, name
print("发布清单与指纹一致：%s 个文件" % len(manifest))
PY
ln -s "$base/shared/.env.production" backend/.env.production
printf 'IMAGE_TAG=%s\nADMIN_IMAGE_TAG=%s\n' "$release" "$release" > .env
docker compose config --quiet
echo '构建新版本镜像，旧服务保持运行。'
docker compose build > "$backup/build.log" 2>&1
docker compose run --rm --no-deps -T backend python - < release-smoke.py > "$backup/preflight.log" 2>&1

echo '备份正式数据库并验证归档目录。'
docker exec postgres16 sh -c 'pg_dump -U "${POSTGRES_USER:-postgres}" -Fc keeping_accounts_prod' > "$backup/database.dump"
[[ -s "$backup/database.dump" ]]
docker exec -i postgres16 pg_restore --list < "$backup/database.dump" > "$backup/database-list.txt"
sha256sum "$backup/database.dump" > "$backup/database.sha256"
chmod 600 "$backup/"*
(cd "$previous" && docker compose exec -T backend python -m alembic current) > "$backup/database-before.log" 2>&1
grep -q '0004_shared' "$backup/database-before.log"

echo '执行 0005_admin、0006_budget 增量迁移并沿用原管理员摘要初始化。'
docker compose run --rm --no-deps -T backend python -m alembic upgrade 0006_budget > "$backup/migration.log" 2>&1
docker compose run --rm --no-deps -T backend python admin_account.py bootstrap > "$backup/admin-bootstrap.log" 2>&1

echo '切换服务端与管理页面。'
activated=1
docker compose up -d --wait --wait-timeout 120 > "$backup/up.log" 2>&1
docker compose exec -T backend python check_connections.py > "$backup/connections-check.log" 2>&1
docker compose exec -T backend python -m alembic current > "$backup/database-after.log" 2>&1
grep -q '0006_budget' "$backup/database-after.log"
healthy=0
for attempt in $(seq 1 20); do
    if curl --fail --silent --show-error --max-time 10 https://mjzs.zf-yun.cn/api/v1/health > "$backup/health.json"; then
        healthy=1
        break
    fi
    sleep 1
done
[[ "$healthy" == 1 ]]
docker compose exec -T backend python - after < release-smoke.py > "$backup/postflight.log" 2>&1
python3 verify-public.py > "$backup/public-assets.log" 2>&1
cmp -s "$backup/env.production" "$base/shared/.env.production"
cmp -s "$backup/nginx.conf" /opt/nginx/conf.d/mjzs.zf-yun.cn.conf
docker exec nginx-web nginx -t > "$backup/nginx-check.log" 2>&1
ln -sfn "$target" "$base/current"
docker inspect moji-backend-1 --format '{{.Config.Image}} {{.Image}}' > "$backup/deployed-backend-image"
docker inspect moji-admin-1 --format '{{.Config.Image}} {{.Image}}' > "$backup/deployed-admin-image"
cp release-manifest.json "$backup/release-manifest.json"
cp release-smoke.py verify-public.py "$backup/"
chmod 600 "$backup/"*
trap - ERR
echo "发布完成：$release"
cat "$backup/database-after.log" "$backup/postflight.log" "$backup/public-assets.log"
docker compose ps
