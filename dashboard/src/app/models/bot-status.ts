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

export interface Score {
  ally: number;
  enemy: number;
}

export interface LastMatch {
  id: string;
  /** LoL: "win" | "loss" | null. */
  result?: string | null;
  /** Valorant: final score. */
  score?: Score | null;
}

/** Match state read by the agent from the game client. */
export interface MatchStatus {
  game: string;
  phase: 'menus' | 'pregame' | 'in_game' | 'post_game' | string;
  queue: string | null;
  score: Score | null;
  match_id: string | null;
  /** Epoch seconds when the bot first saw this match. */
  since: number | null;
  last_match: LastMatch | null;
}

/** live: null until the bot's first check. */
export interface StreamStatus {
  live: boolean | null;
  title?: string;
  viewers?: number;
  started_at?: string | null;
  game?: string | null;
}

export interface BuildInfo {
  commit: string;
  built_at: string;
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
  build?: BuildInfo;
  stream?: StreamStatus;
  match?: MatchStatus | null;
}

export interface WsEvent {
  event: 'status' | 'chat' | string;
  data: BotStatus | ChatMessage | unknown;
  ts?: number;
}
