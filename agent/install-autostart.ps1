# Makes the agent start automatically when the user signs in.
#
#   Right click > "Run with PowerShell"
#   or:  powershell -ExecutionPolicy Bypass -File install-autostart.ps1
#
# No administrator rights needed: the task is created for the current user.
# To remove it:  .\uninstall-autostart.ps1

$ErrorActionPreference = "Stop"

# Do not rename: uninstall-autostart.ps1 finds the installed task by this name.
$taskName = "Twitch Game Agent"
$folder   = $PSScriptRoot
$exe      = Join-Path $folder "twitch-game-agent.exe"
$vbs      = Join-Path $folder "start-hidden.vbs"
$config   = Join-Path $folder "agent.toml"

Write-Host "Installing the agent's automatic start..." -ForegroundColor Cyan

foreach ($file in @($exe, $vbs)) {
    if (-not (Test-Path $file)) {
        Write-Host "[X] Missing $file" -ForegroundColor Red
        Write-Host "    Copy the whole folder, not just the .exe."
        exit 1
    }
}

if (-not (Test-Path $config)) {
    Write-Host "[X] Missing agent.toml" -ForegroundColor Red
    Write-Host "    Copy agent.example.toml to agent.toml and fill in endpoint and token."
    exit 1
}

# Fail early with a clear message if the config is wrong, instead of leaving
# an installed task that will never work.
Write-Host "Testing the configuration..." -ForegroundColor Cyan
& $exe --once
if ($LASTEXITCODE -ne 0) {
    Write-Host "[X] The agent does not start with that configuration (see above)." -ForegroundColor Red
    exit 1
}

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "A task already existed; replacing it." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

$action = New-ScheduledTaskAction -Execute "wscript.exe" `
                                  -Argument ('"{0}"' -f $vbs) `
                                  -WorkingDirectory $folder

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# No time limit and no stopping on battery: it is a background process that
# has to live for the whole session.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
                                         -DontStopIfGoingOnBatteries `
                                         -DontStopOnIdleEnd `
                                         -ExecutionTimeLimit ([TimeSpan]::Zero) `
                                         -RestartCount 3 `
                                         -RestartInterval ([TimeSpan]::FromMinutes(1))

Register-ScheduledTask -TaskName $taskName `
                       -Action $action `
                       -Trigger $trigger `
                       -Settings $settings `
                       -Description "Tells the Twitch bot which game is running." | Out-Null

Start-ScheduledTask -TaskName $taskName

Write-Host ""
Write-Host "[OK] Done. The agent starts with the session and is already running." -ForegroundColor Green
Write-Host ""
Write-Host "  See what it does:   Get-Content '$folder\agent.log' -Tail 20 -Wait"
Write-Host "  Stop it now:        Stop-ScheduledTask -TaskName '$taskName'"
Write-Host "  Remove it for good: .\uninstall-autostart.ps1"
Write-Host ""
Write-Host "It shows up in Task Manager as 'twitch-game-agent.exe'."
