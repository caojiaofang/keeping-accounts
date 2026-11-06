[CmdletBinding()]
param(
    [string]$Release = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ'),
    [switch]$SkipBuild
)
$ErrorActionPreference = 'Stop'
if ($Release -notmatch '^\d{8}T\d{6}Z$') { throw '版本号必须为 UTC 时间格式 yyyyMMddTHHmmssZ。' }
$projectRoot = Split-Path -Parent $PSScriptRoot
$archive = Join-Path $projectRoot "output/moji-$Release.tar.gz"
Push-Location -LiteralPath $projectRoot
try {
    if (-not $SkipBuild) {
        & (Get-Command npm.cmd).Source --prefix admin run build
        if ($LASTEXITCODE -ne 0) { throw '管理端构建失败。' }
    }
    if (-not (Test-Path -LiteralPath 'admin/dist/index.html')) { throw '缺少管理端构建产物。' }
    New-Item -ItemType Directory -Force (Join-Path $projectRoot 'output') | Out-Null
    # 只收集清单中的源码和静态产物，排除实际环境文件、账号文件及本地依赖。
    tar --exclude=__pycache__ --exclude=*.pyc -czf $archive `
        docker-compose.yml backend/app backend/alembic backend/alembic.ini `
        backend/check_connections.py backend/admin_password.py backend/admin_account.py backend/requirements.txt `
        backend/Dockerfile backend/.dockerignore backend/docker-compose.yml backend/deploy `
        admin/Dockerfile admin/.dockerignore admin/deploy admin/dist
    if ($LASTEXITCODE -ne 0) { throw '部署包生成失败。' }
    # Linux 的 sha256sum 会把 CR 当成文件名的一部分，校验清单明确写为 UTF-8/LF。
    $archiveHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    $checksumLine = "$archiveHash  $([IO.Path]::GetFileName($archive))`n"
    [IO.File]::WriteAllText("$archive.sha256", $checksumLine, [Text.UTF8Encoding]::new($false))
    Write-Host "部署包：$archive"
    Write-Host "版本：$Release"
}
finally { Pop-Location }
