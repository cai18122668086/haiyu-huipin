param(
    [string]$RuntimeRoot = (Split-Path -Parent $PSScriptRoot),
    [Parameter(Mandatory=$true)][string]$EnvFile,
    [Parameter(Mandatory=$true)][string]$WorkerRoot,
    [int]$RestartDelaySeconds = 5,
    [int]$MaxRestartDelaySeconds = 300
)
$ErrorActionPreference = 'Stop'
$controlRoot = Join-Path $WorkerRoot 'runtime-control'
$logRoot = Join-Path $WorkerRoot 'logs'
New-Item -ItemType Directory -Force -Path $controlRoot,$logRoot | Out-Null
$supervisorLock = $null
try {
    # The handle is held for the whole supervisor lifetime, including restart delays.
    $supervisorLock = [IO.File]::Open((Join-Path $controlRoot 'supervisor.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    $current = Get-Content -LiteralPath (Join-Path $RuntimeRoot 'current.json') -Raw | ConvertFrom-Json
    if ($current.release_id -notmatch '^[A-Za-z0-9_-]+$') { throw 'Invalid release ID' }
    $releaseRoot = Join-Path $RuntimeRoot (Join-Path 'releases' $current.release_id)
    $python = Join-Path $RuntimeRoot '.venv\Scripts\python.exe'
    $stopFile = Join-Path $controlRoot 'stop.request'
    $env:PYTHONPATH = (Join-Path $releaseRoot 'backend') + [IO.Path]::PathSeparator + $releaseRoot
    $env:PYTHONNOUSERSITE = '1'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    Set-Location -LiteralPath $RuntimeRoot
    & $python -P -B -m worker_host --release-root $releaseRoot --verify-only
    if ($LASTEXITCODE -ne 0) { throw 'Release verification failed' }
    . $EnvFile
    # A private machine config cannot redirect code to a development checkout.
    $env:PYTHONPATH = (Join-Path $releaseRoot 'backend') + [IO.Path]::PathSeparator + $releaseRoot
    while (-not (Test-Path -LiteralPath $stopFile)) {
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $stdout = Join-Path $logRoot "worker-$stamp.out.log"
        $stderr = Join-Path $logRoot "worker-$stamp.err.log"
        $supervisorLog = Join-Path $logRoot ('supervisor-{0:yyyyMMdd}.log' -f (Get-Date))
        Add-Content -LiteralPath $supervisorLog -Value ('{0:O} starting pinned runtime {1}' -f (Get-Date),$current.release_id)
        & $python -P -B -m worker_host --release-root $releaseRoot --worker-root $WorkerRoot 1> $stdout 2> $stderr
        $workerExitCode = $LASTEXITCODE
        Add-Content -LiteralPath $supervisorLog -Value ('{0:O} worker exited {1}' -f (Get-Date),$workerExitCode)
        if (Test-Path -LiteralPath $stopFile) { break }
        # Check the stop request during backoff, so a stopped worker stays stopped.
        for ($second=0; $second -lt $RestartDelaySeconds; $second++) {
            if (Test-Path -LiteralPath $stopFile) { break }
            Start-Sleep -Seconds 1
        }
        $RestartDelaySeconds = [Math]::Min($MaxRestartDelaySeconds,[Math]::Max(5,$RestartDelaySeconds*2))
    }
} finally {
    if ($null -ne $supervisorLock) { $supervisorLock.Dispose() }
}
