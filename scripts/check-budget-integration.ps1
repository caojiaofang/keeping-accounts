[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$projectRoot = Split-Path -Parent $PSScriptRoot
$backendPath = Join-Path $projectRoot 'backend'
$pythonPath = Join-Path $backendPath '.venv/Scripts/python.exe'
$containerName = 'moji-budget-test-' + [Guid]::NewGuid().ToString('N')
$savedIntegrationFlag = $env:MOJI_RUN_INTEGRATION_TESTS
$savedDatabaseUrl = $env:MOJI_BUDGET_MIGRATION_TEST_URL
$savedPythonUtf8 = $env:PYTHONUTF8
$created = $false
$result = 1
try {
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw '缺少后端虚拟环境。' }
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'frontend/node_modules') -PathType Container)) { throw '请先安装前端依赖。' }
    $null = Get-Command node -CommandType Application -ErrorAction Stop
    docker info --format '{{.ServerVersion}}'
    if ($LASTEXITCODE -ne 0) { throw '请先启动 Docker Desktop，再运行隔离预算联调。' }
    # 独立容器、随机回环端口和内存数据目录；不使用现有数据库或服务配置。
    docker run --detach --rm --name $containerName --publish '127.0.0.1::5432' --env POSTGRES_USER=budget_test --env POSTGRES_PASSWORD=budget_test --env POSTGRES_DB=moji_budget_migration_test --tmpfs /var/lib/postgresql/data postgres:17-alpine
    if ($LASTEXITCODE -ne 0) { throw '创建隔离 PostgreSQL 失败。' }
    $created = $true
    $binding = docker port $containerName '5432/tcp'
    if ($LASTEXITCODE -ne 0 -or $binding -notmatch '^127\.0\.0\.1:(\d+)$') { throw '隔离容器端口未绑定到本机回环地址。' }
    $testPort = $Matches[1]
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        docker exec $containerName pg_isready -U budget_test -d moji_budget_migration_test *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Milliseconds 300
    }
    if (-not $ready) { throw '隔离 PostgreSQL 未就绪。' }
    $env:MOJI_RUN_INTEGRATION_TESTS = '1'
    $env:MOJI_BUDGET_MIGRATION_TEST_URL = "postgresql+asyncpg://budget_test:budget_test@127.0.0.1:$testPort/moji_budget_migration_test"
    $env:PYTHONUTF8 = '1'
    Push-Location -LiteralPath $backendPath
    try {
        # 不扩大到依赖远端数据库、Redis 或模型的其他集成测试。
        & $pythonPath -m pytest -q -s tests/test_budget_migrations_integration.py tests/test_budget_plan_integration.py tests/test_budget_months_integration.py tests/test_budget_funds_integration.py tests/test_budget_execution_integration.py tests/test_budget_frontend_integration.py tests/test_monthly_budget_integration.py
        $result = $LASTEXITCODE
        if ($result -ne 0) { throw '隔离预算联调失败，请检查输出。' }
    }
    finally { Pop-Location }
    Write-Host '隔离预算联调通过；临时服务和测试库将在退出时清理。'
}
catch { Write-Error -Message $_.Exception.Message -ErrorAction Continue; $result = 1 }
finally {
    $env:MOJI_RUN_INTEGRATION_TESTS = $savedIntegrationFlag
    $env:MOJI_BUDGET_MIGRATION_TEST_URL = $savedDatabaseUrl
    $env:PYTHONUTF8 = $savedPythonUtf8
    if ($created) {
        docker stop $containerName
        if ($LASTEXITCODE -ne 0) { Write-Error '临时容器清理失败，请核对上方容器名称。' -ErrorAction Continue; $result = 1 }
    }
}
exit $result
