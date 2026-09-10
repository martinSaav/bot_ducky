# Bot de moderación — twitch.tv/chaarqueen

Tres subsistemas en un solo proceso de Python:

| # | Qué hace | Se activa con |
|---|---|---|
| 1 | **Auto-categorizador** — detecta qué juego está corriendo y cambia la categoría de Twitch | `AGENT_TOKEN` y/o Discord |
| 2 | **Bot de chat** — IRC de Twitch con comandos de stats en vivo (LoL / Valorant) | token del rol `bot` |
| 3 | **Pipeline de clips** — clips del día → `yt-dlp` → Google Drive, todos los días | `GDRIVE_FOLDER_ID` + service account |

El auto-categorizador tiene **dos fuentes** y usa la mejor disponible:

| Fuente | Cómo detecta | Prioridad |
|---|---|---|
| **Agente de escritorio** ([`agent/`](agent/README.md), en Rust) | mira los procesos en la PC del streamer | gana |
| **Presencia de Discord** | lee el estado "Jugando a…" | respaldo |

Si el agente deja de reportar por 210 segundos, el bot vuelve solo a Discord.

Cada uno arranca **solo si su configuración está completa**. Podés empezar con
el auto-categorizador y sumar el resto después: el proceso levanta lo que puede
y avisa por log lo que quedó apagado.

---

## Requisitos

- Python 3.10 o superior (probado en 3.14)
- En la PC del streamer, solo el agente de [`agent/`](agent/README.md): un
  ejecutable de ~800 KB sin dependencias de runtime. Es opcional — sin él,
  la categoría se sincroniza por la presencia de Discord.
- Para compilar el agente: Rust (`rustup`, toolchain GNU, sin Visual Studio)

## Instalación

Creá el entorno virtual:

```bash
python -m venv .venv
```

Activalo. Es lo único que cambia según dónde estés:

```bash
source .venv/Scripts/activate        # Windows, Git Bash
source .venv/bin/activate            # Linux / macOS
```

```powershell
.venv/Scripts/Activate.ps1           # Windows, PowerShell
```

> **En PowerShell la primera vez va a fallar** con *"running scripts is disabled
> on this system"*: Windows bloquea los scripts por defecto. Se habilita una sola
> vez y sin permisos de administrador con
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
> Git Bash y Linux no tienen esta restricción.

Con el entorno activo, el resto es igual en todos lados:

```bash
pip install -r requirements.txt
```

```bash
cp .env.example .env
```

> **`cp` sobrescribe sin preguntar.** Este comando es solo para la primera
> vez: si ya tenías un `.env` con credenciales cargadas, lo pisa y las
> perdés. En Git Bash podés usar `cp -n`, que se niega a tocar un archivo
> que ya existe.
>
> Si ya pasó, no está todo perdido: VS Code guarda historial local de cada
> archivo que editaste. Click derecho sobre la pestaña > **Open Timeline**,
> o directamente en `%APPDATA%\Code\User\History`.

Después de completar el `.env`, revisá que todo esté bien antes de arrancar:

```bash
python tools/doctor.py
```

El doctor prueba cada credencial contra su API real y te dice exactamente qué
falta. `[X]` bloquea el arranque, `[!]` solo apaga esa función.

> **Si preferís no activar el entorno**, prefijá cada comando con la ruta al
> intérprete: `.venv/Scripts/python.exe` en Windows, `.venv/bin/python` en Linux.
> En `cmd.exe` además hay que dar vuelta las barras y usar `copy` en vez de `cp`.

---

## Quién es dueño de qué

Todo se crea y se administra desde **tu** cuenta. El streamer no necesita portal
de desarrollo de Twitch ni de Discord.

| Pieza | Se crea en | Qué necesita del streamer |
|---|---|---|
| Aplicación de Twitch (dev.twitch.tv) | tu cuenta | que autorice una vez por OAuth |
| Bot de Discord (portal de desarrolladores) | tu cuenta | su ID de usuario, y compartir un servidor con el bot |
| Cuenta de chat del bot (BotDucky) | cuenta nueva, tuya | nada |
| Riot API / HenrikDev | tu cuenta | su Riot ID (`Nombre#TAG`), que es público |
| Google Cloud + Drive | tu cuenta | nada |
| Agente de escritorio | tu máquina, compilado | que lo deje corriendo en su PC |

El streamer solo hace tres cosas, todas de una sola vez: **autorizar la app de
Twitch** con su cuenta, **entrar al servidor de Discord** y **dejar el agente
instalado**. Nada más pasa por su lado.

Un ID de usuario de Discord o un Riot ID son datos públicos: cualquiera que la
tenga agregada los puede ver. Tenerlos no da ningún acceso a su cuenta.

---

## Configuración

### 1. Aplicación de Twitch

En <https://dev.twitch.tv/console/apps> creá una aplicación:

- **OAuth Redirect URLs**: `http://localhost:3000/callback` — tiene que coincidir
  *carácter por carácter* con `TWITCH_REDIRECT_URI` del `.env`
- **Category**: Chat Bot

Copiá Client ID y Client Secret al `.env`.

### 2. Autorización OAuth

Se autoriza **dos veces**, con dos cuentas distintas:

```bash
python tools/auth_twitch.py broadcaster
```

El broadcaster también necesita el scope `channel:manage:predictions` para
crear predictions. Si agregás esta función a una instalación existente, corré
el comando anterior otra vez para renovar ese permiso.

```bash
python tools/auth_twitch.py bot
```

El script levanta un servidor local, te abre el navegador, captura el `?code=` y
guarda access + refresh token en `data/tokens.json`. **A partir de ahí el refresh
es automático**: Twitch rota el refresh token en cada uso y el bot lo persiste solo.

| Rol | Quién lo corre | Scopes | Para qué |
|---|---|---|---|
| `broadcaster` | **el streamer**, con su cuenta | `channel:manage:broadcast`, `clips:edit` | cambiar categoría y crear clips |
| `bot` | la cuenta que escribe en el chat | `chat:read`, `chat:edit` | leer y responder en el chat |

#### El link no se le puede mandar y ya

El redirect apunta a `http://localhost:3000/callback`, o sea **la máquina donde
corre el script**. Si le mandás el link y ella lo abre en su casa, Twitch la
redirige a *su* localhost, donde no hay nada escuchando: el código se pierde y
nunca llega a tu `data/tokens.json`.

Hay dos formas de resolverlo:

**1. Que inicie sesión en tu máquina** (lo más simple)

Abrís una **ventana privada** del navegador, corrés el script, pegás el link ahí
y ella escribe sus datos. Vos no ves su contraseña, y tu sesión de Twitch queda
intacta porque la ventana privada no la comparte. Al terminar, cerrás la ventana.

**2. Que corra el script en su PC y te mande el resultado**

Necesita Python instalado y que le pases el proyecto con un `.env` que tenga
`TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `TWITCH_CHANNEL` y
`TWITCH_REDIRECT_URI`. Corre `python tools/auth_twitch.py broadcaster` y te manda
el `data/tokens.json` que queda.

Es más incómodo y **le estás dando tu client secret**, que es lo que identifica a
tu aplicación ante Twitch. Con gente de confianza va, pero la opción 1 evita el
problema.

> **El script no guarda nada si la cuenta no coincide.** Si autorizás con la
> cuenta equivocada te avisa y deja tu token anterior intacto, así no perdés uno
> bueno por una sesión abierta de más. Si de verdad querías esa cuenta, `--force`.
>
> Y `run.py` se niega a arrancar si el token de broadcaster no es del canal de
> `TWITCH_CHANNEL`: seguir significaría cambiarle la categoría al canal
> equivocado.

Si vas a usar la cuenta del streamer también para el chat, dejá `TWITCH_BOT_LOGIN`
vacío — igual hay que correr `auth_twitch.py bot` porque son scopes distintos.

### 3. Ponerle nombre al bot

El nombre vive en cuatro lugares distintos, y solo dos son código:

| Dónde | Quién lo ve | Cómo se pone |
|---|---|---|
| Cuenta de chat | el chat, en cada mensaje | registrar una cuenta de Twitch con ese nombre y correr `python tools/auth_twitch.py bot` con ella |
| Nombre de la aplicación | el streamer, al autorizar | campo **Nombre** en dev.twitch.tv |
| Mensajes del bot | el chat | `BOT_NAME` en el `.env` |
| Logs | vos | `BOT_NAME` en el `.env` |

Los mensajes del bot salen directamente con el nombre de su cuenta de Twitch,
sin un prefijo adicional:

```
Categoria actualizada a: VALORANT
Comandos: !lrank !lmatch !vrank !vmatch !uptime !clip
```

`BOT_NAME` se conserva para compatibilidad con configuraciones anteriores, pero
ya no se antepone a los mensajes. **El nombre que el chat ve en cada mensaje es
el de la cuenta de Twitch**.

> **El cambio de categoría no se le atribuye a nadie.** Twitch no expone quién
> modificó la información del canal, ni en la API ni en la interfaz — la
> categoría simplemente cambia. El único rastro visible es el mensaje que el bot
> manda al chat, y eso se apaga con `AUTOCAT_ANNOUNCE=false`.

### 4. Discord (auto-categorizador, fuente de respaldo)

1. Creá una aplicación en <https://discord.com/developers/applications> → **Bot**
2. En la pestaña **Bot** → **Privileged Gateway Intents**, activá **PRESENCE INTENT**
   y **SERVER MEMBERS INTENT**. Sin esto Discord rechaza la conexión del bot.
   `tools/doctor.py` lo verifica conectándose de verdad, no hace falta revisarlo a ojo.
3. Invitá el bot a un servidor privado donde estén solo él y el streamer.
   No necesita ningún permiso: solo estar en el servidor.
4. Con el Modo Desarrollador activado, click derecho sobre el streamer →
   **Copiar ID** → va a `DISCORD_STREAMER_ID`.

Del lado del streamer, en Discord: **Configuración → Actividad de juego →
"Mostrar el juego actual como mensaje de estado"** tiene que estar encendido.
Sin eso no hay presencia que leer.

> Esta fuente es opcional si vas a usar el agente de escritorio, que es más
> confiable. Igual conviene tenerla: cubre los ratos en que la PC de ella no
> tiene el agente corriendo.

**Cómo funciona el cambio de categoría:**

1. Discord reporta `"Jugando a League of Legends"`.
2. El bot espera `PRESENCE_DEBOUNCE_SECONDS` (45 por defecto) y **vuelve a
   verificar** la actividad. Así un alt-tab al launcher no dispara tres cambios.
3. Busca el nombre en `config/game_map.json`; si no está, consulta
   `/helix/search/categories` y acepta el resultado solo si la similitud supera
   0.82. Si no llega, **no toca nada** — prefiere dejar la categoría vieja antes
   que poner una equivocada.
4. Cachea el resultado (los aciertos y los fallos) en `data/state.json`.
5. `PATCH /helix/channels` con el token del broadcaster.

Probalo primero con `AUTOCAT_DRY_RUN=true`: loguea lo que haría sin tocar Twitch.

### 5. Agente de escritorio (fuente principal)

El proyecto [`agent/`](agent/README.md) es un programa chico en Rust que corre en
la PC del streamer y avisa qué juego está abierto mirando los procesos. Es más
confiable que la presencia de Discord: no depende de que ella tenga bien una
configuración ni de que el juego esté en la base de datos de Discord.

**No toca Twitch ni guarda credenciales.** Solo dice "ahora corre VALORANT" y
corta. Funciona con lista blanca: únicamente los juegos declarados en su
`agent.toml` pueden reportarse; cualquier otro programa abierto se descarta en la
máquina y nunca sale de ahí.

Del lado del bot alcanza con generar un secreto compartido:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Ese valor va en `AGENT_TOKEN` del `.env` y en `token` del `agent.toml`. El bot
levanta el endpoint en `AGENT_PORT` (8787 por defecto).

**Red:** como el bot corre en tu PC y el agente en la de ella, hace falta que se
vean. **Tailscale** es lo más simple: se instala en las dos con la misma cuenta y
arma una red privada, sin abrir puertos en el router. La IP que va en el
`agent.toml` sale de `tailscale ip -4`.

Para verificar desde la PC de ella que llega: `http://100.x.y.z:8787/health`
tiene que devolver `{"ok": true, ...}`.

El resto —compilar, configurar, agregar juegos, arranque automático— está en
[`agent/README.md`](agent/README.md).

### 6. Riot API (comandos de LoL)

Pedí la key en <https://developer.riotgames.com>.

> **Ojo con esto:** la *Development Key* **vence cada 24 horas**. Para un bot que
> corre solo, pedí una **Personal API Key** (formulario "Register Product" →
> Personal). Aprueban en días y no expira. Con la de desarrollo vas a tener que
> renovarla a mano todas las mañanas.

`RIOT_ID` es el Riot ID completo (`Nombre#TAG`), no el nombre de invocador viejo.
Para LAS: `RIOT_PLATFORM=la2` y `RIOT_REGION=americas`.

Los comandos usan `Account-V1` (Riot ID → PUUID), `League-V4` (elo) y
`Spectator-V5` (partida en curso), con caché de 90 s / 30 s para no quemar la cuota.

### 7. Valorant (HenrikDev)

Riot no da acceso público a la API de Valorant, así que se usa el wrapper de la
comunidad: <https://docs.henrikdev.xyz>. La key se pide en su Discord y va en
`HENRIK_API_KEY`.

Es una API de terceros: puede cambiar sin aviso. El cliente parsea las respuestas
de forma defensiva y prueba `v3` → `v2` para el rango y `v4` → `v3` para las
partidas, así un cambio de versión de un lado no te deja sin comando.

### 8. Google Drive (pipeline de clips)

1. Creá un proyecto en <https://console.cloud.google.com> y **habilitá la Google Drive API**.
2. **IAM y administración → Cuentas de servicio → Crear**. No hace falta darle roles.
3. En la cuenta creada: **Claves → Agregar clave → JSON**. Guardalo en
   `secrets/service-account.json`.

> ### ⚠️ El error que se come a todo el mundo
>
> **Las service accounts no tienen cuota de almacenamiento en Drive.** Si apuntás
> `GDRIVE_FOLDER_ID` a una carpeta de "Mi unidad" — incluso compartida con permiso
> de editor — la subida falla con `storageQuotaExceeded`.
>
> **La solución:** la carpeta destino tiene que estar en una **Unidad compartida**
> (Shared Drive), con el `client_email` del JSON agregado como **Administrador de
> contenido**. Ahí el dueño de los archivos es la unidad, no la service account.
>
> `tools/doctor.py` detecta esto y te avisa antes de que falle una corrida entera.

El `GDRIVE_FOLDER_ID` es lo que va después de `/folders/` en la URL de la carpeta.
Compartí esa carpeta con el editor y listo.

**Cómo funciona la corrida diaria:**

1. `GET /helix/clips` con `started_at` = ahora − `CLIPS_LOOKBACK_HOURS`, paginando.
2. Descarta los ya subidos (`data/state.json`) y los que no llegan a `CLIPS_MIN_VIEWS`.
3. Ordena por vistas y se queda con los primeros `CLIPS_MAX`.
4. `yt-dlp` baja el `.mp4` (Twitch solo expone la URL del clip, no el archivo).
5. **Subida resumible por fragmentos de 2 MB** a Drive. Si se corta la red,
   le pregunta a Google cuántos bytes recibió y sigue desde ahí, con hasta 5
   reintentos y backoff exponencial. Un microcorte no tira abajo una subida de 200 MB.
6. Borra el archivo local y anota el clip como subido.

El nombre queda `2026-09-08_0142v_Titulo del clip_ClipId.mp4` — ordenado por fecha
y con las vistas al principio, así el editor ve primero lo que más rindió. En la
descripción del archivo van el autor del clip, las vistas y el link original.

---

## Uso

Arrancar todo:

```bash
python run.py
```

Con el bot corriendo, abrí otra terminal en la carpeta del proyecto y ejecutá:

```bash
python tools/chat_console.py
```

Escribí el mensaje y presioná Enter para enviarlo al canal. `:quit` o `:salir`
cierran solamente esa consola. El cliente usa la conexión IRC existente y su
rate limit, así que no abre otra sesión de Twitch.

La presencia de Discord se revalida cada 60 segundos aunque no haya un evento
nuevo. Esto permite detectar que una partida de LoL o Valorant comenzó después
de abrir el juego.

### Resumen del chat

Durante la ejecución, el bot conserva los últimos mensajes en memoria. El
comando `!resumen` muestra una primera versión estructurada:

```text
!resumen       # última hora
!resumen 30m   # últimos 30 minutos
!resumen 2h    # últimas 2 horas
```

Incluye el juego detectado, palabras frecuentes como temas aproximados y los
participantes con más mensajes. Este MVP no recupera mensajes anteriores al
arranque ni usa todavía un LLM; esas son las siguientes capas para PostgreSQL
y el resumen semántico.

Para activar el resumen semántico, configurá `LLM_API_KEY`. `!resumen` usará
los mensajes persistidos en PostgreSQL cuando estén disponibles y volverá al
resumen básico si la API falla o la clave está vacía. El cliente usa una API
compatible con OpenAI, timeout de 45 segundos y reintentos limitados.

Nightbot y la cuenta del propio bot se excluyen automáticamente del historial.
Podés agregar otras cuentas en `CHAT_IGNORED_AUTHORS`, separadas por comas.

Para persistir los mensajes entre reinicios, configurá `DATABASE_URL` con una
base PostgreSQL que tenga `pgvector` instalado. El worker guarda los mensajes
en segundo plano y no bloquea la conexión de Twitch. El esquema inicial está
en `db/001_chat_history.sql`; los embeddings todavía se incorporarán en una
segunda etapa.

Cuando detecta una partida de League of Legends o VALORANT, el bot crea una
prediction de Twitch con las opciones `Gana` y `Pierde`. No crea otra mientras
haya una activa y dura 5 minutos por defecto. En LoL confirma la partida contra
Riot y usa su `gameId` como clave idempotente. En Valorant usa una sesión
persistida del detector, porque la API de HenrikDev configurada en este proyecto
no expone una partida activa con un ID comparable. Por eso Valorant inicia la
prediction basándose en la detección de `VALORANT`, no en una confirmación de
partida: puede dispararse si el juego está abierto en el menú. Al finalizar,
consulta la última partida publicada para anunciar `Partida ganada` o `Partida
perdida`; LoL se vincula con mayor precisión por `gameId`.

Corrida manual de clips, sin esperar al horario:

```bash
python tools/run_clips.py
```

Ver qué clips agarraría, sin descargar ni subir nada:

```bash
python tools/run_clips.py --listar --horas 72
```

Diagnóstico de toda la configuración:

```bash
python tools/doctor.py
```

Los logs van a consola y a `logs/bot.log` (rotativo, 5 archivos de 5 MB).

---

## Comandos del chat

| Comando | Alias | Qué devuelve |
|---|---|---|
| `!lrank` | `!rank` `!elo` `!lol` | Elo de SoloQ y Flex con LP y winrate |
| `!lmatch` | `!partida` `!live` `!game` | Campeón, cola, duración y los 5 enemigos |
| `!vrank` | `!valorant` `!val` | Rango actual, RR, cambio de la última y peak |
| `!vmatch` | `!ultima` | Mapa, agente, K/D/A y resultado de la última |
| `!uptime` | | Tiempo en vivo |
| `!clip` | | Crea un clip (cooldown 30 s) |
| `!comandos` | `!ayuda` | Lista los disponibles |

Los tres primeros aceptan un Riot ID: `!lrank Alguien#LAS` consulta esa cuenta en
vez de la del streamer.

**Solo moderadores:**

| Comando | Qué hace |
|---|---|
| `!categoria <juego>` | Cambia la categoría a mano (sin argumento, muestra la actual) |
| `!auto on` / `!auto off` | Prende y apaga el auto-categorizador en caliente. Sin argumento dice qué fuentes están vivas |
| `!recargar` | Relee `config/game_map.json` sin reiniciar el bot |
| `!vgame` | Confirma manualmente una partida de Valorant e inicia la prediction |
| `!vwin` / `!vloss` | Resuelve manualmente la prediction como victoria o derrota |
| `!vcancel` | Cancela la prediction actual y limpia su estado |

Detalles del comportamiento:

- Los comandos tienen cooldown global (8–30 s según cuál). **Mods y VIPs se lo
  saltean**, para poder corregir algo al toque sin esperar.
- Un comando que no existe, o uno de mod pedido por un viewer, **no responde nada**:
  no tiene sentido spamear el chat con "no tenés permiso".
- `!categoria` a mano le avisa al auto-categorizador, así no te pisa el cambio
  en la siguiente actualización de presencia.
- Si una API se cae, el bot responde el motivo en lenguaje humano y sigue
  funcionando. Ningún error de comando tira el proceso.

---

## `config/game_map.json`

Mapea el nombre que reporta Discord al nombre **exacto** de la categoría en Twitch:

```json
"map": { "counter-strike 2": "Counter-Strike" }
```

Solo hace falta agregar entradas cuando la búsqueda automática falla o es ambigua.
Ya vienen ~40 juegos y una lista de `ignore` (Spotify, VS Code, OBS, navegadores)
para que abrir el editor no te cambie la categoría.

Después de editarlo: `!recargar` en el chat, sin reiniciar.

> **Caso conocido:** TFT corre dentro del cliente de LoL, así que Discord suele
> reportar `"League of Legends"` en vez de `"Teamfight Tactics"`. Si es un problema,
> usá `!categoria Teamfight Tactics` a mano.

---

## Dejarlo corriendo

**Windows (Programador de tareas)** — crear tarea:

- Desencadenador: *Al iniciar el equipo*
- Acción: `C:\Users\Martin\Desktop\projects\twitch\.venv\Scripts\pythonw.exe`
- Argumentos: `run.py`
- Iniciar en: `C:\Users\Martin\Desktop\projects\twitch`
- Marcar *Ejecutar tanto si el usuario inició sesión como si no*

Con `pythonw.exe` no queda una consola abierta; los logs igual van a `logs/bot.log`.

**VPS (Linux, systemd)**

En Linux el intérprete del entorno queda en `.venv/bin/python` (no en
`.venv/Scripts/`). El servicio lo llama por ruta absoluta, así que no hace falta
activar nada. En `/etc/systemd/system/chaarbot.service`:

```ini
[Unit]
Description=Bot de chaarqueen
After=network-online.target

[Service]
WorkingDirectory=/opt/chaarbot
ExecStart=/opt/chaarbot/.venv/bin/python run.py
Restart=always
RestartSec=10
User=chaarbot

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now chaarbot
journalctl -u chaarbot -f
```

Tres cosas a tener en cuenta al mudarlo a un VPS Linux:

- **Redirect URI** — agregá la nueva en dev.twitch.tv, o hacé la autorización en
  tu máquina y copiá `data/tokens.json` al servidor (es más simple).
- **Puerto del agente** — si vas a seguir usando el agente de escritorio, abrí el
  8787 solo hacia la tailnet: `sudo ufw allow in on tailscale0 to any port 8787`.
  No lo expongas a internet: el agente habla HTTP plano, que dentro de Tailscale
  está bien pero afuera no.
- **`ffmpeg` no hace falta** — el pipeline baja los clips en un solo archivo mp4,
  sin necesidad de recombinar pistas.

---

## Problemas comunes

| Síntoma | Causa |
|---|---|
| `Login authentication failed` en el chat | El token del rol `bot` venció o se revocó. El bot lo refresca solo y reconecta; si insiste, volvé a correr `auth_twitch.py bot`. |
| La categoría no cambia nunca | Falta el PRESENCE INTENT, o el streamer tiene apagada la detección de actividad, o no comparte servidor con el bot. Mirá el log: dice cuál de las tres. |
| `storageQuotaExceeded` | La carpeta de Drive no está en una Unidad compartida. Ver la sección de Google Drive. |
| `!lrank` responde que la key venció | La Development Key de Riot dura 24 h. Pedí una Personal Key. |
| El agente no llega al bot | Probá `http://IP:8787/health` desde la PC de ella. Si no responde: Tailscale caído, `AGENT_HOST` no es `0.0.0.0`, o el firewall de Windows bloquea el puerto. |
| El agente reporta pero no cambia nada | Token distinto entre `.env` y `agent.toml` (sale 401 en el log del agente), o `!auto off` activo. |
| `invalid client` al autorizar | La redirect URI del `.env` no coincide exactamente con la de dev.twitch.tv. |
| El bot no responde en el chat | La cuenta del bot puede estar restringida por seguidores/suscriptores. Hacela mod del canal — además sube el límite de envío de 20 a 100 mensajes por 30 s. |

---

## Estructura

```
run.py                      orquestador: levanta los tres subsistemas
config/game_map.json        Discord -> categoría de Twitch
src/
  config.py                 configuración desde .env
  storage.py                JSON con escritura atómica (tokens, estado)
  category.py               resolución de categorías + PATCH al canal
  drive.py                  subida resumible a Google Drive
  services.py               contenedor de dependencias
  gamesource.py             árbitro entre el agente y Discord
  agent_server.py           endpoint HTTP que recibe al agente
  twitch/
    auth.py                 OAuth2, refresh automático, app token
    helix.py                cliente de la API (reintentos, rate limit, 401)
    chat.py                 IRC sobre WebSocket, sin dependencias externas
  discord_presence/bot.py   presencia de Discord (fuente de respaldo)
  riot/lol.py               Account-V1, League-V4, Spectator-V5, Data Dragon
  riot/valorant.py          HenrikDev con fallback de versión
  commands/registry.py      comandos, permisos y cooldowns
  clips/pipeline.py         selección, descarga y subida diaria
agent/                      agente de escritorio en Rust (ver su README)
tools/
  auth_twitch.py            flujo OAuth de un solo uso
  doctor.py                 diagnóstico de toda la configuración
  run_clips.py              corrida manual del pipeline
```

Lo único que corre en la PC del streamer es el agente de `agent/`, que es
opcional y solo reporta juegos de una lista blanca. El resto vive de tu lado.
