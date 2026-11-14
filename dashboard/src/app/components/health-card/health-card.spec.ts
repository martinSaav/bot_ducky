import { healthRows, issueCount } from './health-card';
import { statusFixture } from '../../testing/status.fixture';
import { BotStatus } from '../../models/bot-status';

function agentRow(s: BotStatus) {
  return healthRows(s).find(row => row.name === 'Desktop agent')!;
}

describe('healthRows', () => {
  it('reports everything normal for a healthy bot', () => {
    expect(issueCount(statusFixture())).toBe(0);
  });

  it('shows a silent agent as an error while she is live', () => {
    const s = statusFixture({
      sources: { agent: { game: null, match_id: null, stale: true, age_seconds: 600, stale_after: 210 } },
    });
    expect(agentRow(s)).toMatchObject({ level: 'error', detail: 'silent · last report 10 min ago' });
  });

  it('shows a silent agent as idle while the stream is offline', () => {
    const s = statusFixture({
      stream: { live: false },
      sources: { agent: { game: null, match_id: null, stale: true, age_seconds: 7200, stale_after: 210 } },
    });
    expect(agentRow(s)).toMatchObject({ level: 'off', detail: 'idle · last report 2 h ago' });
  });

  it('flags an expired Riot key', () => {
    const s = statusFixture({ riot: { lol: { configured: false, key_dead: true }, valorant: { configured: true } } });
    const riot = healthRows(s).find(row => row.name === 'Riot API')!;
    expect(riot).toMatchObject({ level: 'error', detail: 'key expired · renew it' });
    expect(issueCount(s)).toBe(1);
  });

  it('warns when the agent on her PC is older than this bot expects', () => {
    const s = statusFixture({
      agent: { version: '0.1.0', host: 'DESKTOP-KPSHI8S', expected: '0.2.0', outdated: true },
    });
    const row = agentRow(s);
    expect(row.level).toBe('warn');
    expect(row.detail).toContain('v0.1.0');
    expect(row.detail).toContain('update to 0.2.0');
  });

  it('shows the agent version when it is up to date', () => {
    const s = statusFixture({
      agent: { version: '0.2.0', host: 'DESKTOP-KPSHI8S', expected: '0.2.0', outdated: false },
    });
    expect(agentRow(s)).toMatchObject({ level: 'ok', detail: 'VALORANT · 2 s ago · v0.2.0' });
  });

  it('leaves the agent row as it is when no agent has connected yet', () => {
    expect(agentRow(statusFixture({ agent: null })).detail).toBe('VALORANT · 2 s ago');
  });
});
