<#
    stop.ps1 - safely stops the local catcher process.
    Does NOT touch OCI resources. Never deletes an instance or any cloud resource.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$Root       = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $Root 'var\runtime'
$PidFile    = Join-Path $RuntimeDir 'catcher.pid'
$LockFile   = Join-Path $RuntimeDir 'catcher.lock'
$StateFile  = Join-Path $RuntimeDir 'state.json'

if (-not (Test-Path $PidFile)) {
    Write-Host 'Catcher is not running (no PID file).' -ForegroundColor Yellow
    Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
    exit 0
}

$procId = (Get-Content $PidFile -Raw).Trim()
$proc = $null
try { $proc = Get-Process -Id ([int]$procId) -ErrorAction Stop } catch { }

if (-not $proc) {
    Write-Host "Process $procId is already dead - cleaning up runtime files." -ForegroundColor Yellow
    Remove-Item $PidFile, $LockFile -Force -ErrorAction SilentlyContinue
    exit 0
}

Write-Host "Stopping the catcher (PID $procId)..." -ForegroundColor Cyan
Stop-Process -Id ([int]$procId) -Force -ErrorAction Stop

for ($i = 0; $i -lt 20; $i++) {
    try { $null = Get-Process -Id ([int]$procId) -ErrorAction Stop } catch { break }
    Start-Sleep -Milliseconds 250
}

Remove-Item $PidFile, $LockFile -Force -ErrorAction SilentlyContinue

if (Test-Path $StateFile) {
    try {
        $state = @{}
        (Get-Content $StateFile -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $state[$_.Name] = $_.Value }
        $state['status']    = 'STOPPED'
        $state['stoppedAt'] = (Get-Date -Format 'o')
        ($state | ConvertTo-Json -Depth 6) | Set-Content -Path $StateFile -Encoding UTF8
    } catch { }
}

Write-Host 'Catcher stopped. No OCI resources were changed.' -ForegroundColor Green
