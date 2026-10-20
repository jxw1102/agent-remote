# Agent Remote - install agentremoted on Windows.
#
#   powershell -ExecutionPolicy Bypass -File daemon\scripts\install-windows.ps1
#   powershell -ExecutionPolicy Bypass -File daemon\scripts\install-windows.ps1 -Provider grok -Port 8473
#
# Without -Provider the harnesses installed on PATH are detected and enabled
# (claude, grok, codex, cursor-agent, copilot, codebuddy, dsh); claude is the
# fallback when none is found.
#   powershell -ExecutionPolicy Bypass -File daemon\scripts\install-windows.ps1 -Uninstall
#
# No admin rights: the daemon runs from a Scheduled Task at logon, which
# also restarts it if it exits. Data lives in %USERPROFILE%\.agentremoted.
param(
    [string]$Provider = "",
    [int]$Port = 8473,
    [string]$Bind = "127.0.0.1",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$HomeDir = Join-Path $env:USERPROFILE ".agentremoted"
$TaskName = "agentremoted"

if ($Uninstall) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task $TaskName. Data kept in $HomeDir."
    exit 0
}

$DaemonDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

# -Provider is a seed for config.json, not a Windows requirement: the daemon
# mounts only the harnesses listed there. Detect the installed CLIs so a
# fresh box does not end up with a provider it does not have.
if (-not $Provider) {
    $candidates = @(
        @{ name = "claude";    bin = "claude" },
        @{ name = "grok";      bin = "grok" },
        @{ name = "codex";     bin = "codex" },
        @{ name = "cursor";    bin = "cursor-agent" },
        @{ name = "copilot";   bin = "copilot" },
        @{ name = "codebuddy"; bin = "codebuddy" },
        @{ name = "deepseek";  bin = "dsh" }
    )
    $found = @()
    foreach ($c in $candidates) {
        if (Get-Command $c.bin -ErrorAction SilentlyContinue) { $found += $c.name }
    }
    if ($found.Count -eq 0) {
        $found = @("claude")
        Write-Warning "No harness CLI found on PATH; enabling claude. Install one and re-run with -Provider."
    }
    $Provider = ($found -join ",")
    Write-Host "Detected harnesses: $Provider"
}

# Find a real python.exe (the py launcher is a wrapper, not an executable).
$PyExe = ""
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) { $PyExe = $py.Source }
if (-not $PyExe) {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $PyExe = (& py -3 -c "import sys; print(sys.executable)") 2>$null
    }
}
if (-not $PyExe) { throw "Python 3 not found on PATH. Install it from python.org first." }

New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null

$cfgPath = Join-Path $HomeDir "config.json"
if (-not (Test-Path $cfgPath)) {
    $providers = @($Provider.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $cfg = [ordered]@{
        providers   = $providers
        port        = $Port
        bind        = $Bind
        turn_timeout = 1800
    }
    $cfg | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 $cfgPath
    Write-Host "Wrote $cfgPath (providers=$Provider port=$Port)"
} else {
    Write-Host "Keeping existing $cfgPath"
}

$tokPath = Join-Path $HomeDir "token"
if (-not (Test-Path $tokPath)) {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $tok = [Convert]::ToBase64String($bytes).Replace("+","").Replace("/","").Replace("=","")
    if ($tok.Length -gt 43) { $tok = $tok.Substring(0,43) }
    Set-Content -NoNewline -Encoding ASCII $tokPath $tok
    Write-Host "Generated token in $tokPath"
}

# The task runs this launcher: it pins the env the service needs.
$runCmd = Join-Path $HomeDir "run-agentremoted.cmd"
@(
    "@echo off"
    "set AGENTREMOTED_HOME=%USERPROFILE%\.agentremoted"
    "set PYTHONPATH=$DaemonDir"
    "cd /d `"$DaemonDir`""
    "`"$PyExe`" -m agentremoted"
) | Set-Content -Encoding ASCII $runCmd

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$runCmd`"" -WorkingDirectory $DaemonDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
try { $settings.RestartCount = 999; $settings.RestartInterval = (New-TimeSpan -Minutes 1) } catch {}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 2

$token = Get-Content $tokPath -Raw
$base = "http://$($Bind):$Port"
Write-Host ""
Write-Host "agentremoted installed as scheduled task $TaskName"
Write-Host "  Base URL : $base"
Write-Host "  Token    : $token"
Write-Host "  Home     : $HomeDir"
Write-Host "  Logs     : Task Scheduler > $TaskName"
Write-Host ""
Write-Host "Point an Agent Remote client at the Base URL with that token."
