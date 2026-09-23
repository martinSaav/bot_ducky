import { Component, inject } from '@angular/core';
import { AsyncPipe, NgClass, KeyValuePipe } from '@angular/common';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { BotApiService } from '../../services/bot-api';
import { Source } from '../../models/bot-status';

@Component({
  selector: 'app-sources-card',
  standalone: true,
  imports: [AsyncPipe, NgClass, KeyValuePipe, MatCardModule, MatIconModule],
  template: `
    @if (api.status$ | async; as s) {
      <mat-card class="dash-card">
        <mat-card-header>
          <mat-icon mat-card-avatar class="card-icon">sensors</mat-icon>
          <mat-card-title>Fuentes</mat-card-title>
          <mat-card-subtitle>Agente y Discord</mat-card-subtitle>
        </mat-card-header>
        <mat-card-content>
          @for (entry of s.sources | keyvalue; track entry.key) {
            <div class="source-row" [class.stale]="entry.value.stale">
              <div class="source-header">
                <mat-icon class="src-icon" [ngClass]="entry.key">{{ sourceIcon(entry.key) }}</mat-icon>
                <span class="source-name">{{ entry.key }}</span>
                <span class="status-dot" [class.active]="!entry.value.stale" [class.stale]="entry.value.stale"></span>
                <span class="status-label" [class.active]="!entry.value.stale" [class.stale]="entry.value.stale">
                  {{ entry.value.stale ? 'stale' : 'activo' }}
                </span>
              </div>
              <div class="source-details">
                <span class="detail-item">
                  <span class="detail-label">Juego</span>
                  <span class="detail-val">{{ entry.value.game ?? '–' }}</span>
                </span>
                @if (entry.value.match_id) {
                  <span class="detail-item">
                    <span class="detail-label">Match ID</span>
                    <span class="detail-val mono">{{ entry.value.match_id }}</span>
                  </span>
                }
                <span class="detail-item">
                  <span class="detail-label">Último reporte</span>
                  <span class="detail-val mono">hace {{ entry.value.age_seconds }}s</span>
                </span>
                @if (entry.value.stale_after) {
                  <span class="detail-item">
                    <span class="detail-label">Stale after</span>
                    <span class="detail-val mono">{{ entry.value.stale_after }}s</span>
                  </span>
                }
              </div>
            </div>
          } @empty {
            <p class="empty">Sin fuentes reportando</p>
          }
        </mat-card-content>
      </mat-card>
    }
  `,
  styles: [`
    .source-row { padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.06); }
    .source-row:last-child { border-bottom: none; }
    .source-row.stale { opacity: 0.5; }
    .source-header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
    .source-name { font-weight: 600; font-size: 14px; text-transform: capitalize; flex: 1; }
    .src-icon { font-size: 18px; height: 18px; width: 18px; }
    .src-icon.agent { color: #a78bfa; }
    .src-icon.discord { color: #818cf8; }
    .status-dot { width: 8px; height: 8px; border-radius: 50%; }
    .status-dot.active { background: #34d399; box-shadow: 0 0 6px #34d399; animation: pulse 2s infinite; }
    .status-dot.stale { background: #6b7280; }
    .status-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; }
    .status-label.active { color: #34d399; }
    .status-label.stale { color: #6b7280; }
    .source-details { display: flex; flex-wrap: wrap; gap: 12px; padding-left: 26px; }
    .detail-item { display: flex; flex-direction: column; gap: 1px; }
    .detail-label { font-size: 10px; color: rgba(255,255,255,0.4); text-transform: uppercase; }
    .detail-val { font-size: 12px; color: rgba(255,255,255,0.85); }
    .mono { font-family: monospace; font-size: 11px; }
    .empty { color: rgba(255,255,255,0.3); font-size: 13px; }
    @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
  `]
})
export class SourcesCardComponent {
  api = inject(BotApiService);
  sourceIcon(name: string): string {
    return name === 'agent' ? 'computer' : 'chat_bubble';
  }
}
