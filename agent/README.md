# Agente de escritorio

Programa chico en Rust que corre en la PC del streamer. Cada 5 segundos mira si
hay algún juego de una lista corriendo y le avisa al bot, que es quien cambia la
categoría del canal.

**No toca Twitch.** No guarda tokens, no tiene el client secret, no sabe nada de
la cuenta de nadie. Solo dice "ahora está corriendo VALORANT" y corta.

## Por qué existe

La presencia de Discord funciona, pero depende de que ella tenga activada la
detección de actividad, de que Discord esté abierto, y de que el juego esté en la
base de datos de Discord. Mirar el proceso es directo: si `cs2.exe` está en
memoria, está jugando CS2.

El agente **le gana** a Discord cuando los dos están activos. Si el agente se cae
o la PC lo tiene apagado, el bot vuelve solo a la presencia de Discord después de
210 segundos sin noticias.

## Qué se manda, exactamente

```json
{
  "source": "agent",
  "game": "VALORANT",
  "exe": "VALORANT-Win64-Shipping.exe",
  "match_id": "8b51c8b3-...",
  "metadata": null,
  "host": "PC-DE-CHAAR",
  "agent_version": "0.1.0",
  "sent_at": "2026-09-09T22:16:19Z"
}
```

Eso es todo. **Funciona con lista blanca:** solo los juegos declarados en
`agent.toml` pueden aparecer en ese campo. Cualquier otro programa abierto se
descarta en el momento y nunca sale de la máquina. No se manda la lista de
procesos, ni títulos de ventana, ni nada más.

Se manda cuando el juego cambia, y como latido cada 60 segundos. Siempre es el
estado completo ("juega X" / "no juega nada"), nunca un cambio incremental: si se
pierde un mensaje, el siguiente latido lo corrige solo.

## Compilar

> **El agente es solo para Windows.** Detecta procesos por nombre de `.exe` y
> pide la hora a `kernel32`, así que no compila en Linux. Es a propósito: corre
> en la PC donde se juega. El bot, en cambio, corre igual en Windows o en un VPS
> Linux — el agente le habla por HTTP y no le importa el sistema del otro lado.

```bash
cargo build --release
```

Queda en `target/release/twitch-game-agent.exe` (~800 KB, sin dependencias de
runtime — no hace falta instalar nada en la PC de destino).

Si usás el toolchain `x86_64-pc-windows-gnu` de rustup sin Visual Studio, no
necesitás compilador de C: las dependencias están elegidas para evitarlo. Por eso
el tiempo se resuelve con llamadas a kernel32 y las consultas HTTPS a las APIs locales
de Riot (Valorant y LoL) se delegan a PowerShell, evitando compilar dependencias de TLS pesadas en Rust.

## Configurar

```bash
cp agent.example.toml agent.toml
```

> Igual que con el `.env` del bot: `cp` pisa el destino sin avisar. Si ya
> tenías un `agent.toml` configurado, usá `cp -n` o copialo a mano.

Dos cosas obligatorias:

- **`endpoint`** — la URL del bot. Con Tailscale, `tailscale ip -4` en la PC donde
  corre el bot te da la IP: `http://100.x.y.z:8787/game`
- **`token`** — el mismo valor que `AGENT_TOKEN` en el `.env` del bot. Generalo con:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Probar antes de dejarlo suelto

```bash
./twitch-game-agent.exe --once
```

Dice qué detecta ahora mismo y sale. Con un juego abierto tiene que nombrarlo.

```bash
./twitch-game-agent.exe --scan
```

Lista los procesos en ejecución para averiguar el ejecutable de un juego que
falte. **Solo imprime en pantalla, no manda nada a ningún lado.**

### Agregar un juego

Abrí el juego, corré `--scan`, buscá el ejecutable y agregalo:

```toml
[[games]]
name = "Silksong"        # nombre EXACTO de la categoría en Twitch
priority = 20
processes = ["Silksong.exe"]
```

`priority` desempata cuando hay dos coincidencias a la vez. Por eso el cliente de
Riot está en 10 y la partida en 20: con los dos abiertos, gana la partida.

## Dejarlo corriendo solo

```powershell
powershell -ExecutionPolicy Bypass -File install-autostart.ps1
```

El `-ExecutionPolicy Bypass` no es opcional: Windows bloquea los scripts de
PowerShell por defecto y sin eso falla con *"running scripts is disabled on this
system"*. La otra opción es click derecho sobre el `.ps1` > **"Ejecutar con
PowerShell"**, que ya lo hace por su cuenta.

Crea una tarea programada que lo arranca al iniciar sesión, sin ventana. No pide
permisos de administrador. Antes de instalar nada valida la configuración, así no
te queda una tarea que nunca va a andar.

Para sacarlo:

```powershell
powershell -ExecutionPolicy Bypass -File uninstall-autostart.ps1
```

Mientras corre aparece en el Administrador de tareas como `twitch-game-agent.exe`
y se puede cerrar desde ahí. El log queda en `agent.log`, al lado del ejecutable.

## Red

El bot escucha en el puerto 8787. Como corre en la PC de Martín y el agente en la
de ella, hace falta que una llegue a la otra. **Tailscale** es lo más simple:
instalarlo en las dos PCs con la misma cuenta y listo — arma una red privada, sin
abrir puertos en el router ni exponer nada a internet.

Para verificar que se ven, desde la PC de ella:

```
http://100.x.y.z:8787/health
```

Tiene que devolver `{"ok": true, ...}`.

**Sobre TLS:** el binario se compila sin soporte de HTTPS a propósito. El túnel de
Tailscale ya va cifrado con WireGuard, así que HTTP adentro de la tailnet no viaja
en claro por internet. Si algún día el bot pasa a un VPS con URL pública, hay que
ponerle un proxy inverso con TLS adelante, o recompilar `ureq` con la feature
`native-tls`. El agente rechaza un `endpoint` con `https://` en vez de fallar de
forma rara al primer envío.

## Estructura

```
src/main.rs      CLI, bucle principal y log
src/config.rs    lectura y validación de agent.toml
src/detect.rs    enumeración de procesos y lista blanca
src/report.rs    envío HTTP al bot
src/clock.rs     fecha y hora vía kernel32, sin dependencias
src/valorant.rs  extracción de partida activa vía Riot LCU API
src/lol.rs       extracción de datos en vivo vía Live Client Data API
```
