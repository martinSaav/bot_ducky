import { Component, inject } from '@angular/core';
import { AsyncPipe } from '@angular/common';
import { MatIconModule } from '@angular/material/icon';
import { BotApiService } from '../../services/bot-api';
import { BotStatus } from '../../models/bot-status';

type Level = 'ok' | 'warn' | 'error' | 'off';

interface HealthRow {
  name: string;
  icon: string;
  level: Level;
  detail: string;
}

/** Every part of the bot in one list: what works, what needs attention, what is off. */
@Component({
  selector: 'app-health-card',
  standalone: true,
  imports: [AsyncPipe, MatIconModule],
  template: `
    <section class="panel">
      <header class="card-head">
        <mat-icon>monitor_heart</mat-icon>
        <h2>System health</h2>
        @if (api.status$ | async; as s) {
          @let issues = issueCount(s);
          <span class="card-meta" [class.attention]="issues > 0">
            {{ issues === 0 ? 'All systems normal' : issues + (issues === 1 ? ' needs attention' : ' need attention') }}
          </span>
        }
      </header>

      @if (api.status$ | async; as s) {
        <ul class="rows">
          @for (row of rows(s); track row.name) {
            <li [class]="row.level">
              <span class="dot"></span>
              <mat-icon class="row-icon">{{ row.icon }}</mat-icon>
              <span class="name">{{ row.name }}</span>
              <span class="detail">{{ row.detail }}</span>
            </li>
          }
        </ul>
      } @else {
        <div class="placeholder">Loading…</div>
      }
    </section>
  `,
  styles: [`
    .rows { list-style: none; margin: 0; padding: 0; }
    .rows li { display: grid; grid-template-columns: 10px 20px 1fr auto; align-items: center; gap: 10px;
      padding: 9px 2px; border-bottom: 1px solid var(--border); font-size: 13px; }
    .rows li:last-child { border-bottom: none; }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--off); }
    .ok .dot { background: var(--ok); box-shadow: 0 0 6px var(--ok); }
    .warn .dot { background: var(--warn); }
    .error .dot { background: var(--err); box-shadow: 0 0 6px var(--err); }
    .row-icon { font-size: 18px; width: 18px; height: 18px; color: var(--text-3); }
    .name { color: var(--text-1); font-weight: 500; }
    .detail { color: var(--text-3); text-align: right; font-size: 12px; }
    .warn .detail { color: var(--warn); }
    .error .detail { color: var(--err); }
    .off .name { color: var(--text-3); }
  `]
})
export class HealthCardComponent {
  api = inject(BotApiService);

  issueCount(s: BotStatus): number {
    return this.rows(s).filter(row => row.level === 'warn' || row.level === 'error').length;
  }

  rows(s: BotStatus): HealthRow[] {
    const sub = s.subsystems;
    const lol = s.riot.lol;
    return [
      this.source(s, 'agent', 'Desktop agent', 'computer', sub.agent),
      this.source(s, 'discord', 'Discord presence', 'headset_mic', sub.discord_presence),
      {
        name: 'Auto-categorizer', icon: 'category',
        level: s.autocat_enabled ? 'ok' : 'warn',
        detail: s.autocat_enabled
          ? `on · following ${s.current_source ?? 'no source'}`
          : 'off · !auto on to resume',
      },
      {
        name: 'Chat bot', icon: 'chat',
        level: sub.chat_bot ? 'ok' : 'error',
        detail: sub.chat_bot ? `connected as ${s.bot_login}` : 'not running',
      },
      {
        name: 'Riot API', icon: 'shield',
        level: lol.key_dead ? 'error' : lol.configured ? 'ok' : 'off',
        detail: lol.key_dead ? 'key expired · renew it' : lol.configured ? 'LoL stats' : 'not configured',
      },
      this.toggle('HenrikDev', 'sports_esports', s.riot.valorant.configured, 'Valorant stats'),
      this.toggle('LLM', 'smart_toy', sub.llm_summary, 'summaries and !pregunta'),
      this.toggle('Database', 'storage', sub.database, 'chat history'),
      this.toggle('Chat memory', 'hub', sub.embeddings, '!pregunta ready'),
      this.toggle('Clips to Drive', 'movie', sub.clips_pipeline, 'daily upload'),
    ];
  }

  private source(s: BotStatus, key: string, name: string, icon: string, enabled: boolean): HealthRow {
    const src = s.sources[key];
    if (!enabled) return { name, icon, level: 'off', detail: 'not configured' };
    // Off stream, a silent source is expected (her PC or Discord may be off):
    // it only needs attention while she is live.
    const live = s.stream?.live !== false;
    if (!src) return { name, icon, level: live ? 'warn' : 'off', detail: live ? 'no reports yet' : 'idle' };
    if (src.stale) {
      return live
        ? { name, icon, level: 'error', detail: `silent · last report ${ago(src.age_seconds)}` }
        : { name, icon, level: 'off', detail: `idle · last report ${ago(src.age_seconds)}` };
    }
    return { name, icon, level: 'ok', detail: `${src.game ?? 'no game'} · ${ago(src.age_seconds)}` };
  }

  private toggle(name: string, icon: string, on: boolean, detail: string): HealthRow {
    return { name, icon, level: on ? 'ok' : 'off', detail: on ? detail : 'not configured' };
  }
}

function ago(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)} s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  return `${Math.round(seconds / 3600)} h ago`;
}
