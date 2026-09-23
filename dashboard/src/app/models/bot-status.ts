export interface Source {
  game: string | null;
  match_id: string | null;
  stale: boolean;
  age_seconds: number;
  stale_after: number | null;
}

export interface PredictionOutcomes {
  [key: string]: string;
}

export interface Prediction {
  match_key: string;
  prediction_id: string;
  game: string;
  outcomes: PredictionOutcomes;
}

export interface LolStatus {
  configured: boolean;
  key_dead: boolean;
}

export interface ValStatus {
  configured: boolean;
}

export interface RiotStatus {
  lol: LolStatus;
  valorant: ValStatus;
}

export interface Subsystems {
  chat_bot: boolean;
  discord_presence: boolean;
  agent: boolean;
  clips_pipeline: boolean;
  llm_summary: boolean;
  database: boolean;
  embeddings: boolean;
}

export interface ChatMessage {
  author: string;
  text: string;
  ts: number;
}

export interface BotStatus {
  channel: string;
  bot_login: string;
  uptime_seconds: number;
  current_game: string | null;
  current_source: string | null;
  autocat_enabled: boolean;
  sources: Record<string, Source>;
  prediction: Prediction | null;
  chat_recent: ChatMessage[];
  chat_total_in_memory: number;
  subsystems: Subsystems;
  riot: RiotStatus;
  ws_clients: number;
  snapshot_at: number;
}

export interface WsEvent {
  event: 'status' | 'chat' | string;
  data: BotStatus | ChatMessage | unknown;
  ts?: number;
}
