param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$taskName = "USStockMonitoringBot"
$project = (Resolve-Path $PSScriptRoot).Path
$runner = Join-Path $project "run_discord_bot.ps1"
$startupFile = Join-Path ([Environment]::GetFolderPath("Startup")) "USStockMonitoringBot.cmd"

function Stop-USMarketBot {
    Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -like "*us-stock-monitoring-bot*run_discord_bot.ps1*"
    } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

if ($Uninstall) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $startupFile -Force -ErrorAction SilentlyContinue
    Stop-USMarketBot
    Write-Host "removed: $taskName and startup fallback"
    exit 0
}

if (-not (Test-Path (Join-Path $project ".env"))) {
    throw ".env 파일이 없습니다. Discord와 Alpaca 설정을 먼저 입력하세요."
}

$python = Join-Path $project ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        & $launcher.Source -m venv (Join-Path $project ".venv")
    } else {
        $launcher = Get-Command python -ErrorAction Stop
        & $launcher.Source -m venv (Join-Path $project ".venv")
    }
}

& $python -m pip install --disable-pip-version-check -q -r (Join-Path $project "requirements.txt")

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`"" -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
Stop-USMarketBot
try {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "Daily US market briefing Discord bot" -Force -ErrorAction Stop | Out-Null
    Remove-Item -LiteralPath $startupFile -Force -ErrorAction SilentlyContinue
    Start-ScheduledTask -TaskName $taskName
    Write-Host "installed scheduled task: $taskName"
} catch {
    Write-Warning "예약 작업 권한이 없어 현재 사용자 시작프로그램으로 등록합니다."
    $command = "start `"`" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$runner`""
    Set-Content -LiteralPath $startupFile -Value @("@echo off", $command) -Encoding ASCII
    Start-Process -FilePath "powershell.exe" -WindowStyle Hidden -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$runner`"")
    Write-Host "installed startup fallback: $startupFile"
}
Write-Host "runtime: $project"

