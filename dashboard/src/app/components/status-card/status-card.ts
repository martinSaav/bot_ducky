import { Component, inject } from '@angular/core';
import { AsyncPipe, NgClass } from '@angular/common';
import { MatCardModule } from '@angular/material/card';
import { MatChipsModule } from '@angular/material/chips';
import { MatIconModule } from '@angular/material/icon';
import { BotApiService } from '../../services/bot-api';

@Component({
  selector: 'app-status-card',
  standalone: true,
  imports: [AsyncPipe, NgClass, MatCardModule, MatChipsModule, MatIconModule],
  template: `
    @if (api.status$ | async; as s) {
      <mat-card class="dash-card status-card">
        <mat-card-header>
          <mat-icon mat-card-avatar class="card-icon">videogame_asset</mat-icon>
          <mat-card-title>Estado actual</mat-card-title>
          <mat-card-subtitle>twitch.tv/{{ s.channel }}</mat-card-subtitle>
        </mat-card-header>
        <mat-card-content>
          <div class="stat-row">
            <span class="label">Juego</span>
            <span class="value game-name">{{ s.current_game ?? 'Sin juego' }}</span>
          </div>
          <div class="stat-row">
            <span class="label">Fuente</span>
            <span class="value source-badge" [ngClass]="s.current_source ?? 'none'">
              <mat-icon class="inline-icon">{{ sourceIcon(s.current_source) }}</mat-icon>
              {{ s.current_source ?? '–' }}
            </span>
          </div>
          <div class="stat-row">
            <span class="label">Auto-cat</span>
            <span class="pill" [class.on]="s.autocat_enabled" [class.off]="!s.autocat_enabled">
              {{ s.autocat_enabled ? 'ON' : 'OFF' }}
            </span>
          </div>
          <div class="stat-row">
            <span class="label">Bot login</span>
            <span class="value mono">{{ s.bot_login }}</span>
          </div>
          <div class="stat-row">
            <span class="label">Uptime</span>
            <span class="value mono">{{ formatUptime(s.uptime_seconds) }}</span>
          </div>
          <div class="stat-row">
            <span class="label">WS clients</span>
            <span class="value mono">{{ s.ws_clients }}</span>
          </div>
        </mat-card-content>
      </mat-card>
    } @else {
      <mat-card class="dash-card status-card loading">
        <mat-card-content>
          <span class="loading-text">Conectando con el bot…</span>
        </mat-card-content>
      </mat-card>
    }
  `,
  styles: [`
    .stat-row { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.06); }
    .stat-row:last-child { border-bottom: none; }
    .label { color: rgba(255,255,255,0.5); font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; }
    .value { font-size: 14px; font-weight: 500; }
    .game-name { color: #c084fc; font-size: 16px; font-weight: 600; }
    .mono { font-family: monospace; }
    .pill { padding: 2px 10px; border-radius: 12px; font-size: 11px; font-weight: 700; letter-spacing: 0.1em; }
    .pill.on { background: rgba(52,211,153,0.2); color: #34d399; border: 1px solid rgba(52,211,153,0.4); }
    .pill.off { background: rgba(239,68,68,0.2); color: #f87171; border: 1px solid rgba(239,68,68,0.3); }
    .source-badge { display: flex; align-items: center; gap: 4px; }
    .source-badge.agent { color: #a78bfa; }
    .source-badge.discord { color: #818cf8; }
    .inline-icon { font-size: 16px; height: 16px; width: 16px; }
    .loading { opacity: 0.5; }
    .loading-text { color: rgba(255,255,255,0.4); font-size: 14px; }
  `]
})
export class StatusCardComponent {
  api = inject(BotApiService);

  sourceIcon(source: string | null): string {
    if (source === 'agent') return 'computer';
    if (source === 'discord') return 'chat_bubble';
    return 'help_outline';
  }

  formatUptime(seconds: number): string {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  }
}
