param(
    [Parameter(Mandatory=$true)][string]$Bundle,
    [Parameter(Mandatory=$true)][string]$Destination,
    [Parameter(Mandatory=$true)][string]$PythonExecutable
)
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $Destination) { throw 'Destination already exists; refuse overwrite.' }
$base = [IO.Path]::GetFullPath($Destination).TrimEnd('\') + '\'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($Bundle)
try {
    foreach ($entry in $zip.Entries) {
        $target = [IO.Path]::GetFullPath((Join-Path $Destination $entry.FullName))
        if (-not $target.StartsWith($base,[StringComparison]::OrdinalIgnoreCase)) { throw 'Bundle entry escapes destination.' }
    }
} finally { $zip.Dispose() }
Expand-Archive -LiteralPath $Bundle -DestinationPath $Destination
$current = Get-Content -LiteralPath (Join-Path $Destination 'current.json') -Raw | ConvertFrom-Json
if ($current.release_id -notmatch '^[A-Za-z0-9_-]+$') { throw 'Invalid release ID' }
$release = Join-Path $Destination (Join-Path 'releases' $current.release_id)
$expected = Get-Content -LiteralPath (Join-Path $Destination 'environment.json') -Raw | ConvertFrom-Json
$actual = & $PythonExecutable -c 'import sys; print(sys.version)'
if ($LASTEXITCODE -ne 0 -or $actual.Trim() -ne $expected.python.Trim()) { throw 'Install the exact recorded Python build before continuing.' }
& $PythonExecutable -m venv (Join-Path $Destination '.venv')
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
$python = Join-Path $Destination '.venv\Scripts\python.exe'
& $python -m pip install --requirement (Join-Path $release 'requirements.lock')
if ($LASTEXITCODE -ne 0) { throw 'Locked dependency installation failed' }
$env:PYTHONPATH = (Join-Path $release 'backend') + [IO.Path]::PathSeparator + $release
$env:PYTHONNOUSERSITE = '1'
& $python -P -B -m worker_host --release-root $release --verify-only
if ($LASTEXITCODE -ne 0) { throw 'Source verification failed' }
Write-Output 'Runtime installed. Enroll this device separately; no production worker was started.'
