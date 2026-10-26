[CmdletBinding()]
param(
    [switch]$Build
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$projectRoot = Split-Path -Parent $PSScriptRoot
$frontendPath = Join-Path $projectRoot 'frontend'
$backendPath = Join-Path $projectRoot 'backend'
$pythonPath = Join-Path $backendPath '.venv/Scripts/python.exe'

function Invoke-Check {
    param(
        [string]$Name,
        [string]$Directory,
        [string]$Command,
        [string[]]$Arguments
    )

    Write-Host "`n>>> $Name"
    Push-Location -LiteralPath $Directory
    try {
        & $Command @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$Name 失败（退出码：$LASTEXITCODE）。"
        }
    }
    finally {
        Pop-Location
    }
}

# 统一入口固定运行本地检查；真实数据库和模型验证仍通过文档中的专用命令启用。
$savedIntegrationFlag = $env:MOJI_RUN_INTEGRATION_TESTS
$savedLlmFlag = $env:MOJI_RUN_LLM_TESTS
try {
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
        throw '缺少 backend/.venv/Scripts/python.exe，请先按后端说明配置虚拟环境。'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $frontendPath 'node_modules') -PathType Container)) {
        throw '缺少 frontend/node_modules，请先在 frontend 目录运行 npm ci。'
    }
    $npmPath = (Get-Command npm.cmd -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    $env:MOJI_RUN_INTEGRATION_TESTS = '0'
    $env:MOJI_RUN_LLM_TESTS = '0'

    Invoke-Check -Name '前端检查' -Directory $frontendPath -Command $npmPath -Arguments @('run', 'check')
    Invoke-Check -Name '后端测试' -Directory $backendPath -Command $pythonPath -Arguments @('-m', 'pytest', '-q')
    Invoke-Check -Name '后端代码检查' -Directory $backendPath -Command $pythonPath -Arguments @('-m', 'ruff', 'check', '.')
    Invoke-Check -Name '后端格式检查' -Directory $backendPath -Command $pythonPath -Arguments @('-m', 'ruff', 'format', '--check', '.')
    Invoke-Check -Name '后端依赖检查' -Directory $backendPath -Command $pythonPath -Arguments @('-m', 'pip', 'check')

    if ($Build) {
        Invoke-Check -Name '微信小程序构建' -Directory $frontendPath -Command $npmPath -Arguments @('run', 'build:mp-weixin')
        Invoke-Check -Name 'H5 构建' -Directory $frontendPath -Command $npmPath -Arguments @('run', 'build:h5')
    }
    Write-Host "`n全部检查通过。"
}
catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
}
finally {
    $env:MOJI_RUN_INTEGRATION_TESTS = $savedIntegrationFlag
    $env:MOJI_RUN_LLM_TESTS = $savedLlmFlag
}
