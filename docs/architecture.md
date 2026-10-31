# System Architecture

This diagram shows the project's three main subsystems and how they interact with external services (Twitch, Discord, Riot, Google Drive and the desktop agent), all running inside the same Python process.

```mermaid
flowchart TD
    subgraph pc_streamer ["Streamer's PC"]
        Agente["Desktop Agent (Rust)"]
    end

    subgraph bot_ducky ["Bot Ducky - Main Python Process"]

        subgraph sub_auto ["Subsystem 1: Auto-categorizer"]
            S["Agent Server"]
            D["Discord Presence"]
            C["Decision Logic"]
            S --> |"Priority 1"| C
            D --> |"Fallback"| C
        end

        subgraph sub_chat ["Subsystem 2: Chat Bot"]
            T["Twitch IRC Client"]
            R["Riot API Client"]
            E["Memory / Embeddings"]
            T <--> E
            T --> R
        end

        subgraph sub_clips ["Subsystem 3: Clips Pipeline"]
            P["yt-dlp Downloader"]
            G["Google Drive Client"]
            P --> G
        end
    end

    %% Auto-categorizer external connections
    Agente -- "Sends the running game and match state" --> S
    DiscordAPI["Discord API"] -. "'Playing ...' status" .-> D
    C -- "Updates the category" --> TwitchAPI["Twitch API"]

    %% Bot external connections
    TwitchChat["Twitch Chat"] <--> |"Commands and messages"| T
    R <--> |"LoL/Valorant stats"| RiotAPI["Riot API"]

    %% Pipeline external connections
    TwitchAPI -- "Fetches the day's clips" --> P
    G -- "Uploads .mp4 files" --> Drive["Google Drive API"]

    classDef python fill:#2b5b84,stroke:#fff,stroke-width:2px,color:#fff;
    classDef external fill:#4a4a4a,stroke:#333,stroke-width:2px,color:#fff;
    classDef rust fill:#dea584,stroke:#333,stroke-width:2px,color:#000;

    class S,D,C,T,R,E,P,G python;
    class TwitchAPI,TwitchChat,RiotAPI,Drive,DiscordAPI external;
    class Agente rust;
```

For how the agent reads each game's match state, see [`agent.md`](agent.md). For the chat embeddings flow, see [`embeddings.md`](embeddings.md).
