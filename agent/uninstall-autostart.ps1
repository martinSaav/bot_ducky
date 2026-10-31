# Removes the agent from automatic start and closes it if it is running.
#
#   Right click > "Run with PowerShell"

$ErrorActionPreference = "Stop"
# Must match the name used by install-autostart.ps1.
$taskName = "Twitch Game Agent"

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "[OK] Scheduled task removed." -ForegroundColor Green
} else {
    Write-Host "No task was installed." -ForegroundColor Yellow
}

$processes = Get-Process -Name "twitch-game-agent" -ErrorAction SilentlyContinue
if ($processes) {
    $processes | Stop-Process -Force
    Write-Host "[OK] Agent closed." -ForegroundColor Green
} else {
    Write-Host "The agent was not running." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Done, nothing is left starting the agent automatically."
Write-Host "The files are still in the folder; delete it if you no longer need it."
