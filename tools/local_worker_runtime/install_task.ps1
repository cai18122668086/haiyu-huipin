param(
    [Parameter(Mandatory=$true)][string]$RuntimeRoot,
    [Parameter(Mandatory=$true)][string]$EnvFile,
    [Parameter(Mandatory=$true)][string]$WorkerRoot,
    [string]$TaskName = 'PolarisCommerceLocalWorker'
)
$ErrorActionPreference = 'Stop'
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $existing -and $existing.State -eq 'Running') {
    throw 'Drain and stop the existing worker first; installer never terminates tasks.'
}
foreach ($path in @((Join-Path $RuntimeRoot 'current.json'),(Join-Path $RuntimeRoot '.venv\Scripts\python.exe'),$EnvFile)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Required file missing: $path" }
}
$launcher = Join-Path $RuntimeRoot 'bin\run_worker.ps1'
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$launcher`" -RuntimeRoot `"$RuntimeRoot`" -EnvFile `"$EnvFile`" -WorkerRoot `"$WorkerRoot`""
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $RuntimeRoot
if ($null -ne $existing) {
    Set-ScheduledTask -TaskName $TaskName -Action $action | Out-Null
} else {
    $trigger = New-ScheduledTaskTrigger -AtLogOn -RandomDelay (New-TimeSpan -Seconds 30)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Pinned Polaris local worker runtime' | Out-Null
}
Write-Output "Installed $TaskName action. No worker was started."
