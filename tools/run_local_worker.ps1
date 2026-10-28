param(
    [string]$EnvFile = "D:\Polaris\local-worker\worker.env.ps1",
    [string]$WorkerRoot = "D:\Polaris\local-worker",
    [int]$RestartDelaySeconds = 5,
    [int]$MaxRestartDelaySeconds = 300
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "backend"
$python = Join-Path $backendRoot ".venv\Scripts\python.exe"
$logRoot = Join-Path $WorkerRoot "logs"

New-Item -ItemType Directory -Force -Path $WorkerRoot, $logRoot | Out-Null

function Write-SupervisorLog([string]$Message) {
    $logFile = Join-Path $logRoot ("supervisor-{0:yyyyMMdd}.log" -f (Get-Date))
    Add-Content -LiteralPath $logFile -Value ("{0:O} {1}" -f (Get-Date), $Message)
}

if (-not (Test-Path -LiteralPath $python)) {
    Write-SupervisorLog "worker python runtime missing: $python"
    exit 2
}
if (-not (Test-Path -LiteralPath $EnvFile)) {
    Write-SupervisorLog "worker env file missing: $EnvFile"
    exit 2
}

. $EnvFile
$required = @(
    "POLARIS_WORKER_API_URL",
    "POLARIS_WORKER_PROFILE_ROOT",
    "POLARIS_WORKER_CAPABILITIES"
)
$missing = @(
    $required | Where-Object {
        [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($_))
    }
)
if ($missing.Count -gt 0) {
    Write-SupervisorLog ("worker env missing required keys: " + ($missing -join ","))
    exit 2
}
$workerId = [Environment]::GetEnvironmentVariable("POLARIS_WORKER_ID")
$enrollmentToken = [Environment]::GetEnvironmentVariable("POLARIS_WORKER_ENROLLMENT_TOKEN")
$deviceId = [Environment]::GetEnvironmentVariable("POLARIS_WORKER_DEVICE_ID")
if ([string]::IsNullOrWhiteSpace($workerId) -and [string]::IsNullOrWhiteSpace($enrollmentToken)) {
    Write-SupervisorLog "worker id or enrollment token is required"
    exit 2
}
if (-not [string]::IsNullOrWhiteSpace($enrollmentToken) -and [string]::IsNullOrWhiteSpace($deviceId)) {
    Write-SupervisorLog "worker device id is required for enrollment"
    exit 2
}
$tokenFile = [Environment]::GetEnvironmentVariable("POLARIS_WORKER_SESSION_TOKEN_FILE")
$token = [Environment]::GetEnvironmentVariable("POLARIS_WORKER_SESSION_TOKEN")
if ([string]::IsNullOrWhiteSpace($token) -and
    [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable("POLARIS_WORKER_ENROLLMENT_TOKEN")) -and
    ([string]::IsNullOrWhiteSpace($tokenFile) -or -not (Test-Path -LiteralPath $tokenFile))) {
    Write-SupervisorLog "worker session token, enrollment token and token file are all missing"
    exit 2
}

while ($true) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $stdout = Join-Path $logRoot "worker-$stamp.out.log"
    $stderr = Join-Path $logRoot "worker-$stamp.err.log"
    try {
        Write-SupervisorLog "starting local worker"
        # Run in the supervisor's process tree so Scheduled Task stop/restart
        # also reaps the Worker child instead of leaving an orphan instance.
        & $python -m app.ecommerce_local_worker 1> $stdout 2> $stderr
        $exitCode = $LASTEXITCODE
        Write-SupervisorLog "local worker exited with code $exitCode; restarting"
    } catch {
        Write-SupervisorLog ("worker supervisor error: " + $_.Exception.Message)
    }
    Start-Sleep -Seconds $RestartDelaySeconds
    $RestartDelaySeconds = [Math]::Min(
        $MaxRestartDelaySeconds,
        [Math]::Max(5, $RestartDelaySeconds * 2)
    )
}
