import { Component, inject, OnInit, signal } from '@angular/core';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { BotApiService } from '../../services/bot-api';
import { timer, switchMap } from 'rxjs';

@Component({
  selector: 'app-logs-panel',
  standalone: true,
  imports: [MatCardModule, MatIconModule],
  template: `
    <mat-card class="dash-card logs-card">
      <mat-card-header>
        <mat-icon mat-card-avatar class="card-icon">terminal</mat-icon>
        <mat-card-title>Logs</mat-card-title>
        <mat-card-subtitle>Últimas 80 líneas de bot.log</mat-card-subtitle>
      </mat-card-header>
      <mat-card-content>
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
    .log-container { max-height: 360px; overflow-y: auto; font-family: 'Roboto Mono', monospace; font-size: 11px; line-height: 1.6; }
    .log-container::-webkit-scrollbar { width: 4px; }
    .log-container::-webkit-scrollbar-thumb { background: rgba(167,139,250,0.3); border-radius: 2px; }
    .log-line { padding: 1px 4px; border-radius: 2px; word-break: break-all; white-space: pre-wrap; color: rgba(255,255,255,0.65); }
    .log-line.error { color: #f87171; background: rgba(239,68,68,0.06); }
    .log-line.warning { color: #fbbf24; }
    .log-line.info { color: rgba(255,255,255,0.7); }
    .log-line.debug { color: rgba(255,255,255,0.35); }
    .empty-log { color: rgba(255,255,255,0.3); font-size: 13px; padding: 12px 0; text-align: center; }
  `]
})
export class LogsPanelComponent implements OnInit {
  private api = inject(BotApiService);
  lines = signal<string[]>([]);

  ngOnInit(): void {
    // Refresh logs every 10 seconds
    timer(0, 10000).pipe(
      switchMap(() => this.api.getLogs(80))
    ).subscribe(res => this.lines.set(res.lines));
  }

  lineClass(line: string): string {
    const l = line.toLowerCase();
    if (l.includes(' error') || l.includes('exception') || l.includes('critical')) return 'error';
    if (l.includes(' warning')) return 'warning';
    if (l.includes(' debug')) return 'debug';
    return 'info';
  }
}
