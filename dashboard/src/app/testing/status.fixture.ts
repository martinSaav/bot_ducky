import { BotStatus } from '../models/bot-status';

/** A healthy, live status snapshot; each test overrides only what it checks. */
export function statusFixture(overrides: Partial<BotStatus> = {}): BotStatus {
  return {
    channel: 'chaarqueen',
    bot_login: 'botduckytv',
    uptime_seconds: 1834,
    current_game: 'VALORANT',
    current_source: 'agent',
    autocat_enabled: true,
    sources: {
      agent: { game: 'VALORANT', match_id: 'val-1', stale: false, age_seconds: 2, stale_after: 210 },
      discord: { game: 'VALORANT', match_id: null, stale: false, age_seconds: 31, stale_after: null },
    },
    prediction: null,
    chat_recent: [],
    chat_total_in_memory: 0,
    subsystems: {
      chat_bot: true, discord_presence: true, agent: true, clips_pipeline: true,
      llm_summary: true, database: true, embeddings: true,
    },
    riot: { lol: { configured: true, key_dead: false }, valorant: { configured: true } },
    ws_clients: 1,
    snapshot_at: 0,
    build: { commit: '8437feb', built_at: '2026-11-14T05:00:46Z' },
    stream: { live: true, title: 'ranked', viewers: 42, started_at: '2026-11-14T03:00:00Z', game: 'VALORANT' },
    match: null,
    chat: { per_minute: 0, last_hour: 0 },
    agent: null,
    ...overrides,
  };
}
