# Deja el agente arrancando solo al iniciar sesion.
#
#   Click derecho > "Ejecutar con PowerShell"
#   o:  powershell -ExecutionPolicy Bypass -File install-autostart.ps1
#
# No necesita permisos de administrador: la tarea se crea para el usuario
# actual. Para sacarla:  .\uninstall-autostart.ps1

$ErrorActionPreference = "Stop"

$nombreTarea = "Twitch Game Agent"
$carpeta     = $PSScriptRoot
$exe         = Join-Path $carpeta "twitch-game-agent.exe"
$vbs         = Join-Path $carpeta "start-hidden.vbs"
$config      = Join-Path $carpeta "agent.toml"

Write-Host "Instalando el arranque automatico del agente..." -ForegroundColor Cyan

foreach ($archivo in @($exe, $vbs)) {
    if (-not (Test-Path $archivo)) {
        Write-Host "[X] Falta $archivo" -ForegroundColor Red
        Write-Host "    Copia la carpeta entera, no solo el .exe."
        exit 1
    }
}

if (-not (Test-Path $config)) {
    Write-Host "[X] Falta agent.toml" -ForegroundColor Red
    Write-Host "    Copia agent.example.toml a agent.toml y completa endpoint y token."
    exit 1
}

# Falla temprano y con un mensaje claro si la config esta mal, en vez de
# dejar una tarea instalada que no va a funcionar nunca.
Write-Host "Probando la configuracion..." -ForegroundColor Cyan
& $exe --once
if ($LASTEXITCODE -ne 0) {
    Write-Host "[X] El agente no arranca con esa configuracion (ver arriba)." -ForegroundColor Red
    exit 1
}

$existente = Get-ScheduledTask -TaskName $nombreTarea -ErrorAction SilentlyContinue
if ($existente) {
    Write-Host "Ya existia una tarea, la reemplazo." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $nombreTarea -Confirm:$false
}

$accion = New-ScheduledTaskAction -Execute "wscript.exe" `
                                  -Argument ('"{0}"' -f $vbs) `
                                  -WorkingDirectory $carpeta

$disparador = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# Sin limite de tiempo y sin parar al desenchufar: es un proceso de fondo
# que tiene que vivir toda la sesion.
$ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
                                        -DontStopIfGoingOnBatteries `
                                        -DontStopOnIdleEnd `
                                        -ExecutionTimeLimit ([TimeSpan]::Zero) `
                                        -RestartCount 3 `
                                        -RestartInterval ([TimeSpan]::FromMinutes(1))

Register-ScheduledTask -TaskName $nombreTarea `
                       -Action $accion `
                       -Trigger $disparador `
                       -Settings $ajustes `
                       -Description "Le avisa al bot de Twitch que juego esta corriendo." | Out-Null

Start-ScheduledTask -TaskName $nombreTarea

Write-Host ""
Write-Host "[OK] Listo. El agente arranca solo con la sesion y ya quedo corriendo." -ForegroundColor Green
Write-Host ""
Write-Host "  Ver que hace:      Get-Content '$carpeta\agent.log' -Tail 20 -Wait"
Write-Host "  Pararlo ahora:     Stop-ScheduledTask -TaskName '$nombreTarea'"
Write-Host "  Sacarlo del todo:  .\uninstall-autostart.ps1"
Write-Host ""
Write-Host "En el Administrador de tareas aparece como 'twitch-game-agent.exe'."
