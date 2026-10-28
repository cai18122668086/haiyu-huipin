param(
    [ValidateSet("Install", "Uninstall", "RunNow")]
    [string]$Action = "Install",
    [string]$TaskName = "PolarisCommerceLocalWorker",
    [string]$WorkerRoot = "D:\Polaris\local-worker"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $repoRoot "tools\run_local_worker.ps1"
$template = Join-Path $repoRoot "tools\local-worker.env.example.ps1"
$envFile = Join-Path $WorkerRoot "worker.env.ps1"

if ($Action -eq "Uninstall") {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task $TaskName. Worker files were preserved at $WorkerRoot."
    exit 0
}

if (-not (Test-Path -LiteralPath $envFile)) {
    New-Item -ItemType Directory -Force -Path $WorkerRoot | Out-Null
    Copy-Item -LiteralPath $template -Destination $envFile
    Write-Host "Created $envFile from the template. Fill in the Worker values, then rerun with -Action Install."
    exit 2
}
if (-not (Test-Path -LiteralPath $runner)) {
    throw "Worker supervisor script not found: $runner"
}

# The env file is local administrator-controlled configuration. Load it so we
# can protect both the rotating token and the browser profile root before the
# SYSTEM task starts. Restricting the directories also protects future atomic
# token replacements, whose temporary file inherits the parent ACL.
. $envFile
$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $existingTask -and $existingTask.State -eq "Running") {
    Stop-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 2
}
function Protect-WorkerPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    New-Item -ItemType Directory -Force -Path $Path | Out-Null
    $currentAccount = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    # A previous SYSTEM run may own rotated token/log files. Reclaiming the
    # exact Worker directory lets an elevated maintenance run repair ACLs.
    & takeown.exe /f $Path /r /d y /a | Out-Null
    & icacls.exe $Path /inheritance:r /grant:r `
        '*S-1-5-18:(OI)(CI)(F)' `
        '*S-1-5-32-544:(OI)(CI)(F)' `
        "${currentAccount}:(OI)(CI)(F)" /T | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to protect Worker path: $Path" }
}

Protect-WorkerPath $WorkerRoot
Protect-WorkerPath ([Environment]::GetEnvironmentVariable("POLARIS_WORKER_PROFILE_ROOT"))

New-Item -ItemType Directory -Force -Path (Join-Path $WorkerRoot "logs") | Out-Null
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$argument = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runner`" -EnvFile `"$envFile`" -WorkerRoot `"$WorkerRoot`""
$taskAction = New-ScheduledTaskAction -Execute $powershell -Argument $argument
$taskTrigger = New-ScheduledTaskTrigger -AtLogOn -RandomDelay (New-TimeSpan -Seconds 30)
$taskSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $taskAction `
    -Trigger $taskTrigger `
    -Settings $taskSettings `
    -Principal $principal `
    -Description "Persistent Polaris Commerce Studio local ecommerce Worker" `
    -Force | Out-Null

Write-Host "Installed $TaskName. It starts when the configured Windows user logs on and restarts after Worker exit."
if ($Action -eq "RunNow") {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Started $TaskName."
}
