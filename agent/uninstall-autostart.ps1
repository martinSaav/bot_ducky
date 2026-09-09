# Saca el agente del arranque automatico y lo cierra si esta corriendo.
#
#   Click derecho > "Ejecutar con PowerShell"

$ErrorActionPreference = "Stop"
$nombreTarea = "Twitch Game Agent"

$tarea = Get-ScheduledTask -TaskName $nombreTarea -ErrorAction SilentlyContinue
if ($tarea) {
    Stop-ScheduledTask -TaskName $nombreTarea -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $nombreTarea -Confirm:$false
    Write-Host "[OK] Tarea programada eliminada." -ForegroundColor Green
} else {
    Write-Host "No habia ninguna tarea instalada." -ForegroundColor Yellow
}

$procesos = Get-Process -Name "twitch-game-agent" -ErrorAction SilentlyContinue
if ($procesos) {
    $procesos | Stop-Process -Force
    Write-Host "[OK] Agente cerrado." -ForegroundColor Green
} else {
    Write-Host "El agente no estaba corriendo." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Listo, no queda nada del agente arrancando solo."
Write-Host "Los archivos siguen en la carpeta; borrala si no la vas a usar mas."
