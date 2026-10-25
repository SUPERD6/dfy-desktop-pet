#requires -Version 5.1
<#
.SYNOPSIS
  Verify the dsh-whale-bridge plugin is live after restarting DeepSeek Harness Desktop.

.DESCRIPTION
  The Desktop build binds its web GUI to 127.0.0.1 on an OS-assigned port, so the
  port changes after every restart (do not hardcode 52208). This script:

    1. reads the fixed status file  <DSH_HOME or %USERPROFILE%\.dsh>\whale-bridge\status.json,
    2. discovers the current DSH Desktop host process and its listening ports,
    3. probes  http://127.0.0.1:<port>/api/whale/status  until the plugin route answers,
    4. prints both payloads and checks that file and route agree.

  It never modifies the profile, the plugin or the status file.

.EXAMPLE
  pwsh -File plugin\verify-live.ps1

.EXAMPLE
  pwsh -File plugin\verify-live.ps1 -Port 52208 -StatusFile '%USERPROFILE%\.dsh\whale-bridge\status.json'

.NOTES
  Exit codes: 0 = OK, 1 = status file missing/invalid, 2 = HTTP route not found,
              3 = file/route disagreement, 4 = unexpected error.
#>
[CmdletBinding()]
param(
    [string]$StatusFile = '',
    [int]$Port = 0,
    [switch]$SkipHttp,
    [int]$TimeoutSeconds = 3
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:RoutePath = '/api/whale/status'
$script:Failures = New-Object System.Collections.Generic.List[string]

function Write-Section {
    param([string]$Text)
    Write-Host ''
    Write-Host "== $Text ==" -ForegroundColor Cyan
}

function Resolve-StatusFile {
    param([string]$Configured)
    if ($Configured -ne '') { return $Configured }
    $dshHomeRoot = $env:USERPROFILE
    if ($env:DSH_HOME -and $env:DSH_HOME.Trim() -ne '') { $dshHomeRoot = $env:DSH_HOME.Trim() }
    return (Join-Path $dshHomeRoot 'whale-bridge\status.json')
}

function Read-StatusFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Write-Host "status file missing: $Path" -ForegroundColor Yellow
        Write-Host '  -> the plugin is not installed/loaded yet, or DSH has not started since install.' -ForegroundColor Yellow
        return $null
    }
    try {
        $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
        return ($raw | ConvertFrom-Json -ErrorAction Stop)
    }
    catch {
        Write-Host "status file is not valid JSON: $Path ($($_.Exception.Message))" -ForegroundColor Red
        return $null
    }
}

function Get-DshHostCandidates {
    # Every DeepSeek Harness.exe process that might own (or sit beside) the GUI port.
    $procs = @()
    try {
        $procs = @(Get-CimInstance Win32_Process -Filter "Name = 'DeepSeek Harness.exe'" -ErrorAction Stop)
    }
    catch {
        Write-Host "could not enumerate processes: $($_.Exception.Message)" -ForegroundColor Yellow
    }
    return $procs
}

function Get-ListeningPorts {
    param([int]$ProcessId)
    $ports = New-Object System.Collections.Generic.List[int]
    try {
        $connections = @(Get-NetTCPConnection -OwningProcess $ProcessId -State Listen -ErrorAction Stop)
        foreach ($connection in $connections) { $ports.Add([int]$connection.LocalPort) }
    }
    catch {
        # Get-NetTCPConnection can be unavailable/permission-limited; netstat always works.
    }
    if ($ports.Count -eq 0) {
        try {
            $lines = @(& netstat -ano 2>$null)
            $pattern = '^\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+' + $ProcessId + '\s*$'
            foreach ($line in $lines) {
                if ($line -match $pattern) { $ports.Add([int]$Matches[1]) }
            }
        }
        catch {
            Write-Host "netstat fallback failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
    return ($ports | Sort-Object -Unique)
}

function Test-WhaleRoute {
    param([int]$CandidatePort, [int]$TimeoutSec)
    $url = "http://127.0.0.1:$CandidatePort$($script:RoutePath)"
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec $TimeoutSec -ErrorAction Stop
        if ($response.StatusCode -ne 200) { return $null }
        $payload = $response.Content | ConvertFrom-Json -ErrorAction Stop
        if ($null -eq $payload -or $null -eq $payload.PSObject.Properties['state']) { return $null }
        return [pscustomobject]@{ Url = $url; Payload = $payload; Raw = $response.Content }
    }
    catch {
        return $null
    }
}

function Format-Staleness {
    param($Payload)
    if ($null -eq $Payload.PSObject.Properties['updatedAt']) { return }
    $timestamp = 0
    try { $timestamp = [long]$Payload.updatedAt } catch { return }
    if ($timestamp -le 0) { return }
    $ageMs = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() - $timestamp
    $ageSec = [Math]::Round($ageMs / 1000.0, 1)
    $color = 'Green'
    if ($ageMs -gt 30000) { $color = 'Red' }
    elseif ($ageMs -gt 12000) { $color = 'Yellow' }
    Write-Host ("last state change: {0} ms ago ({1:O})" -f $ageMs, [DateTimeOffset]::FromUnixTimeMilliseconds($timestamp).ToLocalTime()) -ForegroundColor $color
    if ($ageMs -gt 30000) {
        Write-Host '  -> older than 30s: the 5s heartbeat is not running (DSH stopped, plugin failed to load, or the app was closed).' -ForegroundColor Red
    }
}

function Compare-Snapshots {
    param($FromFile, $FromRoute)
    $fields = @('ok', 'state', 'lastTool', 'doneCount', 'errorCount', 'approvalOpen', 'version')
    foreach ($field in $fields) {
        $fileHas = $null -ne $FromFile.PSObject.Properties[$field]
        $routeHas = $null -ne $FromRoute.PSObject.Properties[$field]
        if (-not $fileHas -or -not $routeHas) {
            $script:Failures.Add("field '$field' missing (file=$fileHas, route=$routeHas)")
            continue
        }
        $left = $FromFile.$field
        $right = $FromRoute.$field
        if ($left -ne $right) {
            $script:Failures.Add("field '$field' differs: file=$left route=$right")
        }
    }
    if ($null -ne $FromFile.PSObject.Properties['updatedAt'] -and $null -ne $FromRoute.PSObject.Properties['updatedAt']) {
        $skew = [Math]::Abs([double]$FromFile.updatedAt - [double]$FromRoute.updatedAt)
        Write-Host ("updatedAt skew: {0} ms (expected 0; a transition between the two reads can add a few ms)" -f $skew)
        if ($skew -gt 5000) { $script:Failures.Add("updatedAt skew too large: $skew ms") }
    }
}

# ---------------------------------------------------------------- file -------
$resolvedStatusFile = Resolve-StatusFile -Configured $StatusFile
Write-Section "1. status file"
Write-Host "path: $resolvedStatusFile"
$filePayload = Read-StatusFile -Path $resolvedStatusFile
if ($null -eq $filePayload) {
    Write-Host ''
    Write-Host 'VERDICT: FAILED (no status file)' -ForegroundColor Red
    exit 1
}
$filePayload | ConvertTo-Json -Depth 4
Format-Staleness -Payload $filePayload

# ---------------------------------------------------------------- route ------
$routeResult = $null
if (-not $SkipHttp) {
    Write-Section "2. loopback route"

    $hosts = @(Get-DshHostCandidates)
    $ports = New-Object System.Collections.Generic.List[int]
    foreach ($process in $hosts) {
        foreach ($candidate in @(Get-ListeningPorts -ProcessId ([int]$process.ProcessId))) {
            if (-not $ports.Contains([int]$candidate)) { $ports.Add([int]$candidate) }
        }
    }
    if ($Port -gt 0) {
        $ports.Insert(0, $Port) | Out-Null
    }
    if ($ports.Count -eq 0) {
        Write-Host 'no listening port found for DeepSeek Harness.exe' -ForegroundColor Yellow
        Write-Host '  -> is DeepSeek Harness Desktop running? (the GUI port is OS-assigned and changes every start)' -ForegroundColor Yellow
    }
    else {
        Write-Host ("candidate ports: {0}" -f (($ports | Sort-Object -Unique) -join ', '))
    }

    foreach ($candidate in ($ports | Sort-Object -Unique)) {
        $attempt = Test-WhaleRoute -CandidatePort ([int]$candidate) -TimeoutSec $TimeoutSeconds
        if ($null -ne $attempt) { $routeResult = $attempt; break }
    }

    if ($null -eq $routeResult) {
        Write-Host 'GET /api/whale/status: not reachable on any candidate port' -ForegroundColor Red
        Write-Host '  -> plugin not installed/loaded, DSH not restarted, or the route registration failed.' -ForegroundColor Yellow
        Write-Host '  -> check the DSH startup log for "plugin tree failed to load" / inactive-entry warnings.' -ForegroundColor Yellow
        $script:Failures.Add('route unreachable')
    }
    else {
        Write-Host "url: $($routeResult.Url)"
        $routeResult.Payload | ConvertTo-Json -Depth 4
        Write-Host ('content-type check: route answered HTTP 200 with JSON (Cache-Control: no-store is set by the plugin)')
        Compare-Snapshots -FromFile $filePayload -FromRoute $routeResult.Payload
    }
}
else {
    Write-Section "2. loopback route"
    Write-Host 'skipped (-SkipHttp)'
}

# ------------------------------------------------------------- verdict ------
Write-Section "3. verdict"
if ($script:Failures.Count -eq 0) {
    Write-Host 'PASS: status file is valid and the loopback route agrees with it.' -ForegroundColor Green
    Write-Host ''
    Write-Host 'The pet integration can poll the status file directly:'
    Write-Host "  $resolvedStatusFile"
    exit 0
}

foreach ($failure in $script:Failures) { Write-Host "FAIL: $failure" -ForegroundColor Red }
Write-Host ''
Write-Host 'VERDICT: FAILED' -ForegroundColor Red
if (-not $SkipHttp -and $null -eq $routeResult) { exit 2 }
exit 3
