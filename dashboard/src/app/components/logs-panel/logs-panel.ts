import { Component, inject, OnInit, signal } from '@angular/core';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { BotApiService } from '../../services/bot-api';
import { timer } from 'rxjs';

@Component({
  selector: 'app-logs-panel',
  standalone: true,
  imports: [MatCardModule, MatIconModule, MatButtonModule],
  template: `
    <mat-card class="dash-card logs-card">
      <mat-card-header>
        <mat-icon mat-card-avatar class="card-icon">terminal</mat-icon>
        <mat-card-title>Logs</mat-card-title>
        <mat-card-subtitle>Últimas 80 líneas de {{ source() === 'agent' ? 'agent.log' : 'bot.log' }}</mat-card-subtitle>
      </mat-card-header>
      <mat-card-content>
        <div class="log-sources">
          <button mat-button [class.selected]="source() === 'agent'" (click)="selectSource('agent')">Agente Rust</button>
          <button mat-button [class.selected]="source() === 'bot'" (click)="selectSource('bot')">Bot</button>
        </div>
        <div class="log-container" #logEl>
          @for (line of lines(); track $index) {
            <div class="log-line" [class]="lineClass(line)">{{ line }}</div>
          } @empty {
            <p class="empty-log">Sin logs disponibles</p>
          }
        </div>
      </mat-card-content>
    </mat-card>
  `,
  styles: [`
    .logs-card { height: 100%; }
    .log-sources { display: flex; gap: 4px; margin-bottom: 8px; }
    .log-sources button.selected { color: #34d399; background: rgba(52,211,153,0.1); }
    .log-container { max-height: 360px; overflow-y: auto; font-family: 'Roboto Mono', monospace; font-size: 11px; line-height: 1.6; }
    .log-container::-webkit-scrollbar { width: 4px; }
    .log-container::-webkit-scrollbar-thumb { background: rgba(167,139,250,0.3); border-radius: 2px; }
    .log-line { padding: 1px 4px; border-radius: 2px; word-break: break-all; white-space: pre-wrap; color: rgba(255,255,255,0.65); }
    .log-line.error { color: #ef4444; background: rgba(239,68,68,0.1); }
    .log-line.warning { color: #fbbf24; background: rgba(251,191,36,0.1); }
    .log-line.info { color: #34d399; }
    .log-line.debug { color: #9ca3af; }
    .empty-log { color: rgba(255,255,255,0.3); font-size: 13px; padding: 12px 0; text-align: center; }
  `]
})
export class LogsPanelComponent implements OnInit {
  private api = inject(BotApiService);
  lines = signal<string[]>([]);
  source = signal<'bot' | 'agent'>('agent');

  ngOnInit(): void {
    // Refresh logs every 10 seconds
    timer(0, 10000).subscribe(() => this.loadLogs(this.source()));
  }

  selectSource(source: 'bot' | 'agent'): void {
    this.source.set(source);
    this.loadLogs(source);
  }

  private loadLogs(source: 'bot' | 'agent'): void {
    this.api.getLogs(80, source).subscribe(res => {
      if (this.source() === source) this.lines.set(res.lines);
    });
  }

  lineClass(line: string): string {
    const l = line.toLowerCase();
    if (l.includes(' error') || l.includes('exception') || l.includes('critical')) return 'error';
    if (l.includes(' warning')) return 'warning';
    if (l.includes(' debug')) return 'debug';
    return 'info';
  }
}
