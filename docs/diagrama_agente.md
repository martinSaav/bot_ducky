# Arquitectura y Flujo: Detección de Valorant

Este documento describe cómo se comunican los distintos componentes del sistema para lograr detectar partidas de Valorant en la PC de la streamer y reportarlas al bot principal.

## Diagrama de Arquitectura de Red

El siguiente diagrama muestra los componentes físicos y de software involucrados. El agente (Daemon) corre en la computadora de la streamer, mientras que el Bot principal corre en un servidor o máquina separada. La comunicación se realiza a través de la red local o una VPN como Tailscale.

```mermaid
graph TD
    subgraph pc_streamer ["PC de la Streamer (Windows)"]
        A["Juego: Valorant"] --- L["Archivo: lockfile"]
        RAPI["API Local de Riot Client<br/>https://127.0.0.1"]
        A -.- RAPI
        
        DAEMON["Rust Daemon<br/>twitch-game-agent.exe"]
        PS["PowerShell"]
        
        L -.->|"1. Lee puerto y password"| DAEMON
        DAEMON -.->|"2. Ejecuta"| PS
        PS <-->|"3. HTTPS GET"| RAPI
    end

    subgraph servidor_bot ["Servidor del Bot (Python)"]
        HTTP["Agent Server<br/>Puerto: 8787"]
        ARBITER["Game Arbiter<br/>Logica de prioridades"]
        TWITCH["Twitch Bot"]
        
        HTTP --> ARBITER
        ARBITER --> TWITCH
    end

    DAEMON == "4. POST HTTP (JSON) con AGENT_TOKEN" === HTTP
    TWITCH == "5. Crea predicción" === API_T[API de Twitch]
```

---

## Diagrama de Secuencia (Flujo Lógico)

Este diagrama detalla exactamente qué sucede cada `X` segundos (configurado por `poll_seconds` en el `agent.toml`).

```mermaid
sequenceDiagram
    autonumber
    participant SYS as "Sistema Operativo"
    participant AGENT as "Rust Daemon"
    participant VAL as "Riot Local API"
    participant SERVER as "Bot: AgentServer"
    participant ARBITER as "Bot: GameArbiter"
    
    loop Cada X Segundos
        AGENT->>SYS: ¿Qué procesos están corriendo?
        SYS-->>AGENT: [chrome.exe, VALORANT-Win64-Shipping.exe, ...]
        
        Note over AGENT: Coincide con 'Valorant' en agent.toml
        
        AGENT->>SYS: Lee %LOCALAPPDATA%\Riot Games\...\lockfile
        SYS-->>AGENT: puerto: 55123, password: XYZ
        
        AGENT->>VAL: GET /chat/v1/session (vía PowerShell)
        VAL-->>AGENT: JSON { subject: "PUUID-1234" }
        
        AGENT->>VAL: GET /core-game/v1/player/PUUID-1234 (vía PowerShell)
        alt En Partida Activa
            VAL-->>AGENT: JSON { matchID: "abc-123" }
        else En Menú / Lobby
            VAL-->>AGENT: JSON { matchID: "" o nulo }
        end
        
        Note over AGENT: Construye el Payload de Reporte
        AGENT->>SERVER: POST /game (Body: game, exe, match_id)
        
        alt Token Inválido
            SERVER-->>AGENT: 401 Unauthorized
        else Token Válido
            SERVER-->>AGENT: 200 OK
            SERVER->>ARBITER: report(game="VALORANT", match_id="abc-123")
            
            Note over ARBITER: Arbiter compara con estado anterior
            alt match_id es nuevo
                ARBITER->>ARBITER: Inicia evento de predicción en Twitch
            end
        end
    end
```

## Resumen del Payload JSON
Cuando el Agente se comunica con el servidor, envía un cuerpo JSON como este:

```json
{
  "source": "agent",
  "game": "VALORANT",
  "match_id": "8b51c8b3-1234-5678-abcd-ef0123456789",
  "exe": "VALORANT-Win64-Shipping.exe",
  "host": "DESKTOP-STREAMER",
  "agent_version": "0.1.0",
  "sent_at": "2026-09-17T23:58:00Z"
}
```

> [!TIP]
> Si el juego es detectado pero no hay una partida activa (la streamer está en el lobby o buscando), el campo `match_id` se envía como `null`. El bot entiende esto como "Valorant está abierto, pero todavía no hay que crear una predicción".
