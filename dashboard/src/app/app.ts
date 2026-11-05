import { Component, inject } from '@angular/core';
import { AsyncPipe, DecimalPipe } from '@angular/common';
import { StatusCardComponent } from './components/status-card/status-card';
import { SourcesCardComponent } from './components/sources-card/sources-card';
import { PredictionCardComponent } from './components/prediction-card/prediction-card';
import { RiotCardComponent } from './components/riot-card/riot-card';
import { SubsystemsCardComponent } from './components/subsystems-card/subsystems-card';
import { ChatFeedComponent } from './components/chat-feed/chat-feed';
import { LogsPanelComponent } from './components/logs-panel/logs-panel';
import { MatchCardComponent } from './components/match-card/match-card';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { BotApiService } from './services/bot-api';
import { ClockService, formatElapsed } from './services/clock';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [
    AsyncPipe,
    DecimalPipe,
    StatusCardComponent,
    SourcesCardComponent,
    PredictionCardComponent,
    RiotCardComponent,
    SubsystemsCardComponent,
    ChatFeedComponent,
    LogsPanelComponent,
    MatchCardComponent,
    MatIconModule,
    MatButtonModule,
  ],
  template: `
    <div class="app-shell">
      <!-- Header: what the stream is doing right now -->
      <header class="topbar">
        <div class="topbar-left">
          <span class="logo-dot"></span>
          <span class="topbar-title">Bot Dashboard</span>
          <span class="topbar-sub">chaarqueen</span>
        </div>

        @if (api.status$ | async; as s) {
          <div class="topbar-stream">
            @if (s.stream?.live === true) {
              <span class="live-badge"><span class="live-dot"></span>LIVE</span>
              <span class="stream-title" [title]="s.stream?.title ?? ''">{{ s.stream?.title }}</span>
              <span class="stream-stat" title="Viewers">
                <mat-icon>visibility</mat-icon>{{ s.stream?.viewers | number }}
              </span>
              @if (s.stream?.started_at; as started) {
                <span class="stream-stat" title="Time live">
                  <mat-icon>schedule</mat-icon>{{ liveFor(started) }}
                </span>
              }
              @if (s.stream?.game; as game) {
                <span class="stream-stat category" title="Twitch category">
                  <mat-icon>category</mat-icon>{{ game }}
                </span>
              }
            } @else if (s.stream?.live === false) {
              <span class="live-badge offline">OFFLINE</span>
            } @else {
              <span class="live-badge unknown">CHECKING…</span>
            }
          </div>

          <span class="build-chip" [title]="s.build?.built_at ? 'Built ' + s.build?.built_at : 'Running code'">
            <mat-icon>commit</mat-icon>{{ s.build?.commit ?? 'unknown' }}
          </span>
        }
      </header>

      <!-- Main grid -->
      <main class="dash-grid">
        <!-- Row 0: the current match, full width -->
        <div class="grid-match">
          <app-match-card />
        </div>

        <!-- Row 1: Status + Sources + Prediction -->
        <div class="grid-col-1">
          <app-status-card />
        </div>
        <div class="grid-col-2">
          <app-sources-card />
        </div>
        <div class="grid-col-3">
          <app-prediction-card />
        </div>

        <!-- Row 2: Subsystems + Riot + Chat -->
        <div class="grid-col-4">
          <app-subsystems-card />
        </div>
        <div class="grid-col-5">
          <app-riot-card />
        </div>
        <div class="grid-col-6">
          <app-chat-feed />
        </div>

        <!-- Row 3: Logs (full width) -->
        <div class="grid-col-full">
          <app-logs-panel />
        </div>
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

  liveFor(startedAt: string): string {
    return formatElapsed((this.clock.now() - Date.parse(startedAt)) / 1000);
  }
}
