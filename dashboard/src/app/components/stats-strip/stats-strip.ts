import { Component, inject } from '@angular/core';
import { AsyncPipe, DecimalPipe } from '@angular/common';
import { MatIconModule } from '@angular/material/icon';
import { BotApiService } from '../../services/bot-api';
import { ClockService, formatElapsed } from '../../services/clock';

/** Headline numbers for the stream, under the header. */
@Component({
  selector: 'app-stats-strip',
  standalone: true,
  imports: [AsyncPipe, DecimalPipe, MatIconModule],
  template: `
    @if (api.status$ | async; as s) {
      <div class="stats">
        <div class="stat">
          <mat-icon>visibility</mat-icon>
          <div>
            <div class="stat-value">{{ s.stream?.live ? (s.stream?.viewers | number) : '—' }}</div>
            <div class="stat-label">Viewers</div>
          </div>
        </div>
        <div class="stat">
          <mat-icon>schedule</mat-icon>
          <div>
            <div class="stat-value">{{ s.stream?.live && s.stream?.started_at ? liveFor(s.stream!.started_at!) : '—' }}</div>
            <div class="stat-label">{{ s.stream?.live ? 'Time live' : 'Offline' }}</div>
          </div>
        </div>
        <div class="stat">
          <mat-icon>speed</mat-icon>
          <div>
            <div class="stat-value">{{ (s.chat?.per_minute ?? 0) | number:'1.0-1' }}</div>
            <div class="stat-label">Messages / min <span class="hint">last 5 min</span></div>
          </div>
        </div>
        <div class="stat">
          <mat-icon>forum</mat-icon>
          <div>
            <div class="stat-value">{{ (s.chat?.last_hour ?? 0) | number }}</div>
            <div class="stat-label">Messages <span class="hint">last hour</span></div>
          </div>
        </div>
      </div>
    } @else {
      <div class="stats">
        @for (i of [1, 2, 3, 4]; track i) {
          <div class="stat skeleton"></div>
        }
      </div>
    }
  `,
  styles: [`
    .stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--gap); }
    .stat {
      display: flex; align-items: center; gap: 14px; padding: 16px 18px; min-height: 76px;
      background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
    }
    .stat mat-icon {
      color: var(--accent-soft); background: var(--accent-bg); border-radius: 10px;
      width: 40px; height: 40px; font-size: 22px; display: flex; align-items: center; justify-content: center;
    }
    .stat-value { font-size: 24px; font-weight: 700; letter-spacing: -0.02em; font-variant-numeric: tabular-nums; color: var(--text-1); }
    .stat-label { font-size: 12px; color: var(--text-3); margin-top: 2px; }
    .hint { color: var(--text-4); }
    .skeleton { background: linear-gradient(90deg, var(--surface) 0%, rgba(255,255,255,0.06) 50%, var(--surface) 100%);
      background-size: 200% 100%; animation: shimmer 1.4s ease-in-out infinite; }
    @keyframes shimmer { from { background-position: 200% 0; } to { background-position: -200% 0; } }
    @media (max-width: 900px) { .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; } }
  `]
})
export class StatsStripComponent {
  api = inject(BotApiService);
  private clock = inject(ClockService);

  liveFor(startedAt: string): string {
    return formatElapsed((this.clock.now() - Date.parse(startedAt)) / 1000);
  }
}
