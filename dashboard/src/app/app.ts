import { Component, inject } from '@angular/core';
import { AsyncPipe, DecimalPipe } from '@angular/common';
import { MatIconModule } from '@angular/material/icon';
import { PredictionCardComponent } from './components/prediction-card/prediction-card';
import { ChatFeedComponent } from './components/chat-feed/chat-feed';
import { LogsPanelComponent } from './components/logs-panel/logs-panel';
import { MatchCardComponent } from './components/match-card/match-card';
import { StatsStripComponent } from './components/stats-strip/stats-strip';
import { HealthCardComponent } from './components/health-card/health-card';
import { BotApiService } from './services/bot-api';
import { ClockService, formatElapsed } from './services/clock';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [
    AsyncPipe,
    DecimalPipe,
    MatIconModule,
    MatchCardComponent,
    StatsStripComponent,
    PredictionCardComponent,
    ChatFeedComponent,
    HealthCardComponent,
    LogsPanelComponent,
  ],
  template: `
    <div class="app-shell">
      <header class="topbar">
        <div class="brand">
          <span class="logo-dot"></span>
          <span class="brand-title">BotDucky</span>
          <span class="brand-sub">twitch.tv/chaarqueen</span>
        </div>

        @if (api.status$ | async; as s) {
          <div class="topbar-stream">
            @if (s.stream?.live === true) {
              <span class="live-badge"><span class="live-dot"></span>LIVE</span>
              <span class="stream-title" [title]="s.stream?.title ?? ''">{{ s.stream?.title }}</span>
              @if (s.stream?.game; as game) {
                <span class="stream-stat" title="Twitch category"><mat-icon>category</mat-icon>{{ game }}</span>
              }
            } @else if (s.stream?.live === false) {
              <span class="live-badge offline">OFFLINE</span>
            } @else {
              <span class="live-badge unknown">CHECKING…</span>
            }
          </div>

          <div class="topbar-meta">
            <span class="updated" [class.stale]="api.problem()">
              <span class="conn-dot"></span>{{ updatedText() }}
            </span>
            <span class="build-chip" [title]="s.build?.built_at ? 'Built ' + s.build?.built_at : 'Running code'">
              <mat-icon>commit</mat-icon>{{ s.build?.commit ?? 'unknown' }}
            </span>
          </div>
        }
      </header>

      @if (api.problem(); as problem) {
        <div class="banner" role="alert">
          <mat-icon>{{ problem === 'unauthorized' ? 'lock' : 'cloud_off' }}</mat-icon>
          @if (problem === 'unauthorized') {
            The bot rejected the dashboard token. Open this page once as /dashboard/?token=&lt;DASHBOARD_TOKEN&gt;.
          } @else {
            Bot unreachable{{ api.lastUpdate() ? ' — showing data from ' + sinceUpdate() + ' ago' : '' }}. Retrying every 5 seconds.
          }
        </div>
      }

      <main class="dash-grid">
        <div class="area-match"><app-match-card /></div>
        <div class="area-stats"><app-stats-strip /></div>
        <div class="area-prediction"><app-prediction-card /></div>
        <div class="area-chat"><app-chat-feed /></div>
        <div class="area-health"><app-health-card /></div>
        <div class="area-logs"><app-logs-panel /></div>
      </main>
    </div>
  `,
  styles: [`
    :host { display: block; min-height: 100vh; }
  `]
})
export class AppComponent {
  api = inject(BotApiService);
  private clock = inject(ClockService);

  sinceUpdate(): string {
    const last = this.api.lastUpdate();
    if (!last) return '';
    const seconds = Math.round((this.clock.now() - last) / 1000);
    if (seconds < 60) return `${seconds} s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
    return formatElapsed(seconds);
  }

  updatedText(): string {
    const last = this.api.lastUpdate();
    if (!last) return 'Connecting…';
    const seconds = Math.max(0, Math.round((this.clock.now() - last) / 1000));
    return seconds < 2 ? 'Updated just now' : `Updated ${seconds}s ago`;
  }
}
