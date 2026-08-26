<#
    status.ps1 - state of the local catcher and of the target instance in OCI.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$Root         = Split-Path -Parent $PSScriptRoot
$OciExe       = Join-Path $Root '.venv\Scripts\oci.exe'
$OciConfig    = Join-Path $Root '.secrets\oci\config'
$ConfigFile   = Join-Path $Root 'config\catcher.psd1'
$RuntimeDir   = Join-Path $Root 'var\runtime'
$PidFile      = Join-Path $RuntimeDir 'catcher.pid'
$StateFile    = Join-Path $RuntimeDir 'state.json'
$ResolvedFile = Join-Path $RuntimeDir 'resolved.json'
$SuccessFile  = Join-Path $RuntimeDir 'success.json'
$LogFile      = Join-Path $Root 'var\logs\catcher.log'

$env:OCI_CLI_CONFIG_FILE = $OciConfig
$env:OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING = 'True'

function Invoke-Oci {
    param([string[]]$Arguments)
    $stamp   = [Guid]::NewGuid().ToString('N')
    $outFile = Join-Path $RuntimeDir "oci-$stamp.out"
    $errFile = Join-Path $RuntimeDir "oci-$stamp.err"
    $line = ($Arguments | ForEach-Object { if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ } }) -join ' '
    try {
        $p = Start-Process -FilePath $OciExe -ArgumentList $line -NoNewWindow -Wait -PassThru `
                 -RedirectStandardOutput $outFile -RedirectStandardError $errFile
        $so = if (Test-Path $outFile) { Get-Content $outFile -Raw } else { '' }
        $se = if (Test-Path $errFile) { Get-Content $errFile -Raw } else { '' }
    } finally {
        Remove-Item $outFile, $errFile -Force -ErrorAction SilentlyContinue
    }
    # Get-Content -Raw on an empty file returns AutomationNull - normalize via interpolation.
    [pscustomobject]@{ ExitCode = $p.ExitCode; StdOut = "$so"; StdErr = "$se" }
}

function Show-Field { param([string]$K, $V)
    Write-Host ("  {0,-18}: " -f $K) -NoNewline
    if ($null -eq $V -or "$V" -eq '') { Write-Host '-' -ForegroundColor DarkGray } else { Write-Host "$V" }
}

Write-Host ''
Write-Host '=== CATCHER (local) ===' -ForegroundColor Cyan

$state = $null
if (Test-Path $StateFile) { try { $state = Get-Content $StateFile -Raw | ConvertFrom-Json } catch { } }

$running = $false
$procId  = $null
if (Test-Path $PidFile) {
    $procId = (Get-Content $PidFile -Raw).Trim()
    try { $null = Get-Process -Id ([int]$procId) -ErrorAction Stop; $running = $true } catch { $running = $false }
}

if ($running) { Write-Host '  RUNNING' -ForegroundColor Green } else { Write-Host '  STOPPED' -ForegroundColor Yellow }
Show-Field 'PID' $(if ($running) { $procId } else { $null })

if ($state) {
    if ($state.startedAt) {
        $started = [datetime]::Parse($state.startedAt)
        $endRef  = if ($running) { Get-Date } elseif ($state.stoppedAt) { [datetime]::Parse($state.stoppedAt) } else { Get-Date }
        $up = $endRef - $started
        Show-Field 'Start'      $started.ToString('yyyy-MM-dd HH:mm:ss')
        Show-Field 'Uptime'     ('{0:00}:{1:00}:{2:00}' -f [int]$up.TotalHours, $up.Minutes, $up.Seconds)
    }
    Show-Field 'Status'        $state.status
    Show-Field 'Rounds'        $state.round
    Show-Field 'Last attempt'  $(if ($state.lastAttemptAt) { ([datetime]::Parse($state.lastAttemptAt)).ToString('yyyy-MM-dd HH:mm:ss') })
    Show-Field 'Last AD'       $state.lastAd
    Show-Field 'Last result'   $state.lastResult
    Show-Field 'Last error'    $(if ($state.lastError) { $state.lastError } else { $state.lastErrorCode })
    Show-Field 'Next attempt'  $(if ($state.nextAttemptAt) { ([datetime]::Parse($state.nextAttemptAt)).ToString('yyyy-MM-dd HH:mm:ss') })
} else {
    Write-Host '  (no state.json - the catcher has not run yet)' -ForegroundColor DarkGray
}

Write-Host ''
Write-Host '=== INSTANCE IN OCI ===' -ForegroundColor Cyan

if (-not (Test-Path $OciConfig) -or -not (Test-Path $ResolvedFile)) {
    Write-Host '  (no OCI config or resolved.json - skipping the OCI query)' -ForegroundColor DarkGray
} else {
    $cfg      = Import-PowerShellDataFile -Path $ConfigFile
    $resolved = Get-Content $ResolvedFile -Raw | ConvertFrom-Json
    $r = Invoke-Oci @('compute','instance','list','--compartment-id',$resolved.CompartmentId,'--display-name',$cfg.DisplayName,'--all','--output','json')
    if ($r.ExitCode -ne 0) {
        Write-Host '  OCI query error:' -ForegroundColor Red
        Write-Host ($r.StdErr.Trim())
    } else {
        $alive = @('PROVISIONING','STARTING','RUNNING','STOPPING','STOPPED')
        $inst = $null
        if ($r.StdOut.Trim()) {
            try { $inst = @(($r.StdOut | ConvertFrom-Json).data | Where-Object { $alive -contains $_.'lifecycle-state' })[0] } catch { }
        }
        if (-not $inst) {
            Write-Host "  VM '$($cfg.DisplayName)': DOES NOT EXIST" -ForegroundColor Yellow
        } else {
            Write-Host "  VM '$($cfg.DisplayName)': EXISTS" -ForegroundColor Green
            Show-Field 'State'  $inst.'lifecycle-state'
            Show-Field 'OCID'   $inst.id
            Show-Field 'AD'     $inst.'availability-domain'
            Show-Field 'Shape'  $inst.shape
            if ($inst.'shape-config') {
                Show-Field 'OCPU / RAM' ("{0} / {1} GB" -f $inst.'shape-config'.ocpus, $inst.'shape-config'.'memory-in-gbs')
            }
            $publicIp = $null
            $v = Invoke-Oci @('compute','instance','list-vnics','--instance-id',$inst.id,'--output','json')
            if ($v.ExitCode -eq 0 -and $v.StdOut.Trim()) {
                try { $publicIp = (@(($v.StdOut | ConvertFrom-Json).data | Where-Object { $_.'public-ip' }) | Select-Object -First 1).'public-ip' } catch { }
            }
            Show-Field 'Public IPv4' $publicIp
            if ($publicIp) {
                Write-Host ''
                Write-Host '  SSH:' -ForegroundColor Cyan
                Write-Host "    ssh -i `".\.secrets\ssh\main-vps`" ubuntu@$publicIp"
            }
        }
    }
}

if (Test-Path $SuccessFile) {
    Write-Host ''
    Write-Host "=== success.json ===" -ForegroundColor Cyan
    Write-Host "  $SuccessFile"
}

if (Test-Path $LogFile) {
    Write-Host ''
    Write-Host '=== LAST 10 LOG LINES ===' -ForegroundColor Cyan
    Get-Content $LogFile -Tail 10 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
}
Write-Host ''
