<#
    start.ps1 - runs the catcher in the background, from the project directory.
    No system service, no Scheduled Task. After a reboot you must start it again.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$Root       = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $Root 'var\runtime'
$LogDir     = Join-Path $Root 'var\logs'
$PidFile    = Join-Path $RuntimeDir 'catcher.pid'
$Catcher    = Join-Path $Root 'scripts\catch-a1.ps1'

foreach ($d in @($RuntimeDir, $LogDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
}

# --- already running? ---
if (Test-Path $PidFile) {
    $oldPid = (Get-Content $PidFile -Raw).Trim()
    $proc = $null
    try { $proc = Get-Process -Id ([int]$oldPid) -ErrorAction Stop } catch { }
    if ($proc) {
        Write-Host "Catcher is already running (PID $oldPid). Not starting a second one." -ForegroundColor Yellow
        Write-Host "  status:  .\scripts\status.ps1"
        Write-Host "  stop:    .\scripts\stop.ps1"
        exit 0
    }
    Write-Host "Found a stale PID file (PID $oldPid is dead) - cleaning up." -ForegroundColor DarkYellow
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $RuntimeDir 'catcher.lock') -Force -ErrorAction SilentlyContinue
}

# Orphaned OCI temp files remain when stop.ps1 kills the process mid-call.
Get-ChildItem $RuntimeDir -File -Filter 'oci-*' -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in '.out', '.err' } |
    Remove-Item -Force -ErrorAction SilentlyContinue

$outLog = Join-Path $LogDir 'catcher.out.log'
$errLog = Join-Path $LogDir 'catcher.err.log'

# IMPORTANT: we launch via WMI (Win32_Process.Create), NOT via Start-Process.
# Start-Process with stream redirection spawns the process directly from this
# shell, so it lands in the same Job Object as the parent terminal. Closing the
# terminal closes the Job Object and Windows kills the whole group - that is how
# the catcher died once (log cut mid-attempt, empty stderr, no [FATAL]).
# WMI creates the process on the WMI service side, outside the Job Object, so the
# catcher keeps running.
$inner   = "& '$Catcher' 1> '$outLog' 2> '$errLog'"
$cmdLine = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"$inner`""

$res = Invoke-CimMethod -ClassName Win32_Process -MethodName Create `
    -Arguments @{ CommandLine = $cmdLine; CurrentDirectory = $Root }

if ($res.ReturnValue -ne 0) {
    Write-Host "Failed to start the catcher via WMI (ReturnValue=$($res.ReturnValue))." -ForegroundColor Red
    exit 1
}

$newPid = [int]$res.ProcessId
Start-Sleep -Seconds 3

$proc = $null
try { $proc = Get-Process -Id $newPid -ErrorAction Stop } catch { }

if (-not $proc) {
    Write-Host "Catcher exited immediately (PID $newPid is already dead)." -ForegroundColor Red
    Write-Host "--- $errLog ---"
    if (Test-Path $errLog) { Get-Content $errLog -Tail 40 }
    Write-Host "--- $outLog ---"
    if (Test-Path $outLog) { Get-Content $outLog -Tail 40 }
    exit 1
}

Write-Host "Catcher started in the background (outside the terminal Job Object). PID: $newPid" -ForegroundColor Green
Write-Host "  log:     $(Join-Path $LogDir 'catcher.log')"
Write-Host "  status:  .\scripts\status.ps1"
Write-Host "  stop:    .\scripts\stop.ps1"
