# Arquitectura General del Sistema

Este diagrama ilustra los tres subsistemas principales descritos en el proyecto y cómo interactúan con servicios externos (Twitch, Discord, Riot, Google Drive y el Agente de escritorio) ejecutándose todos dentro del mismo proceso de Python.

```mermaid
flowchart TD
    subgraph pc_streamer ["PC del Streamer"]
        Agente["Agente de Escritorio (Rust)"]
    end

    subgraph bot_ducky ["Bot Ducky - Proceso Central Python"]
        
        subgraph sub_auto ["Subsistema 1: Auto-categorizador"]
            S["Servidor del Agente"]
            D["Presencia de Discord"]
            C["Lógica de Decisión"]
            S --> |"Prioridad 1"| C
            D --> |"Respaldo (Fallback)"| C
        end
        
        subgraph sub_chat ["Subsistema 2: Bot de Chat"]
            T["Cliente Twitch IRC"]
            R["Cliente API Riot"]
            E["Memoria / Embeddings"]
            T <--> E
            T --> R
        end
        
        subgraph sub_clips ["Subsistema 3: Pipeline de Clips"]
            P["Descargador yt-dlp"]
            G["Cliente Google Drive"]
            P --> G
        end
    end

    %% Conexiones externas Auto-categorizador
    Agente -- "Envía procesos activos" --> S
    DiscordAPI["Discord API"] -. "Estado 'Jugando a...'" .-> D
    C -- "Actualiza categoría" --> TwitchAPI["Twitch API"]

    %% Conexiones externas Bot
    TwitchChat["Chat de Twitch"] <--> |"Comandos y Mensajes"| T
    R <--> |"Stats de LoL/Valorant"| RiotAPI["Riot API"]
    
    %% Conexiones externas Pipeline
    TwitchAPI -- "Obtiene clips del día" --> P
    G -- "Sube archivos .mp4" --> Drive["Google Drive API"]

    classDef python fill:#2b5b84,stroke:#fff,stroke-width:2px,color:#fff;
    classDef external fill:#4a4a4a,stroke:#333,stroke-width:2px,color:#fff;
    classDef rust fill:#dea584,stroke:#333,stroke-width:2px,color:#000;
    
    class S,D,C,T,R,E,P,G python;
    class TwitchAPI,TwitchChat,RiotAPI,Drive,DiscordAPI external;
    class Agente rust;
```
