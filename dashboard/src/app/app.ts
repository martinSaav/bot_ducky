import { Component } from '@angular/core';
import { StatusCardComponent } from './components/status-card/status-card';
import { SourcesCardComponent } from './components/sources-card/sources-card';
import { PredictionCardComponent } from './components/prediction-card/prediction-card';
import { RiotCardComponent } from './components/riot-card/riot-card';
import { SubsystemsCardComponent } from './components/subsystems-card/subsystems-card';
import { ChatFeedComponent } from './components/chat-feed/chat-feed';
import { LogsPanelComponent } from './components/logs-panel/logs-panel';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [
    StatusCardComponent,
    SourcesCardComponent,
    PredictionCardComponent,
    RiotCardComponent,
    SubsystemsCardComponent,
    ChatFeedComponent,
    LogsPanelComponent,
    MatIconModule,
    MatButtonModule,
  ],
  template: `
    <div class="app-shell">
      <!-- Header -->
      <header class="topbar">
        <div class="topbar-left">
          <span class="logo-dot"></span>
          <span class="topbar-title">Bot Dashboard</span>
          <span class="topbar-sub">chaarqueen</span>
        </div>
        <div class="topbar-right">
          <span class="live-badge">
            <span class="live-dot"></span>
            EN VIVO
          </span>
        </div>
      </header>

      <!-- Main grid -->
      <main class="dash-grid">
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
export class AppComponent {}
