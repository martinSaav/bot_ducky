import { Component, inject } from '@angular/core';
import { AsyncPipe, DatePipe } from '@angular/common';
import { MatIconModule } from '@angular/material/icon';
import { BotApiService } from '../../services/bot-api';
import { ClockService, formatElapsed } from '../../services/clock';
import { Prediction } from '../../models/bot-status';

/** The prediction as viewers see it: question, options and the voting countdown. */
@Component({
  selector: 'app-prediction-card',
  standalone: true,
  imports: [AsyncPipe, DatePipe, MatIconModule],
  template: `
    <section class="panel" [class.active]="(api.status$ | async)?.prediction">
      <header class="card-head">
        <mat-icon>how_to_vote</mat-icon>
        <h2>Prediction</h2>
        @if ((api.status$ | async)?.prediction; as p) {
          <span class="card-meta">{{ gameLabel(p.game) }}</span>
        }
      </header>

      @if (api.status$ | async; as s) {
        @if (s.prediction; as p) {
          <p class="question">¿Gana esta partida?</p>
          <div class="outcomes">
            @for (name of outcomeNames(p); track name) {
              <span class="outcome" [class.win]="name === 'Gana'" [class.loss]="name === 'Pierde'">{{ name }}</span>
            }
          </div>

          @if (p.created_at) {
            @let left = secondsLeft(p);
            @if (left > 0) {
              <div class="timing open">
                <span>Voting closes in <strong>{{ format(left) }}</strong></span>
                <span class="opened">opened {{ p.created_at * 1000 | date:'HH:mm' }}</span>
              </div>
              <div class="bar"><div class="fill" [style.width.%]="left / p.window * 100"></div></div>
            } @else {
              <div class="timing locked">
                <mat-icon>lock</mat-icon>
                <span>Voting closed · waiting for the match result</span>
              </div>
              <div class="opened block">opened {{ p.created_at * 1000 | date:'HH:mm' }}</div>
            }
          } @else {
            <div class="timing locked"><mat-icon>lock</mat-icon><span>Waiting for the match result</span></div>
          }
          <p class="help">Resolved automatically when the match ends. By hand: !vwin, !vloss or !vcancel.</p>
        } @else {
          <div class="empty">
            <mat-icon>radio_button_unchecked</mat-icon>
            <div>
              <div class="empty-title">No prediction open</div>
              <div class="empty-sub">Created automatically when a LoL or VALORANT match starts.</div>
            </div>
          </div>
        }
      } @else {
        <div class="placeholder">Loading…</div>
      }
    </section>
  `,
  styles: [`
    .panel.active { border-color: rgba(145, 71, 255, 0.35); }
    .question { font-size: 18px; font-weight: 600; color: var(--text-1); margin: 4px 0 12px; }
    .outcomes { display: flex; gap: 8px; margin-bottom: 16px; }
    .outcome { flex: 1; text-align: center; padding: 10px; border-radius: 10px; font-weight: 600; font-size: 14px;
      background: rgba(255,255,255,0.05); border: 1px solid var(--border); color: var(--text-2); }
    .outcome.win { border-color: rgba(52, 211, 153, 0.35); color: var(--ok); background: rgba(52, 211, 153, 0.08); }
    .outcome.loss { border-color: rgba(248, 113, 113, 0.35); color: var(--err); background: rgba(248, 113, 113, 0.08); }
    .timing { display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: 13px; color: var(--text-2); }
    .timing strong { color: var(--text-1); font-variant-numeric: tabular-nums; }
    .timing.locked { justify-content: flex-start; color: var(--warn); }
    .timing.locked mat-icon { font-size: 16px; width: 16px; height: 16px; }
    .opened { color: var(--text-4); font-size: 12px; }
    .opened.block { margin-top: 4px; }
    .bar { height: 4px; border-radius: 2px; background: rgba(255,255,255,0.08); margin-top: 8px; overflow: hidden; }
    .fill { height: 100%; background: var(--accent); transition: width 1s linear; }
    .help { font-size: 11px; color: var(--text-4); margin: 14px 0 0; }
    .empty { display: flex; align-items: center; gap: 12px; padding: 18px 0; color: var(--text-3); }
    .empty mat-icon { color: var(--text-4); }
    .empty-title { color: var(--text-2); font-weight: 500; font-size: 14px; }
    .empty-sub { font-size: 12px; margin-top: 2px; }
  `]
})
export class PredictionCardComponent {
  api = inject(BotApiService);
  private clock = inject(ClockService);

  gameLabel(game: string): string {
    if (game.includes('valorant')) return 'VALORANT';
    if (game.includes('league')) return 'League of Legends';
    return game;
  }

  /** Twitch keeps the options as Gana / Pierde: stored lowercase as keys. */
  outcomeNames(p: Prediction): string[] {
    return Object.keys(p.outcomes).map(key => key.charAt(0).toUpperCase() + key.slice(1));
  }

  secondsLeft(p: Prediction): number {
    return (p.created_at ?? 0) + p.window - this.clock.now() / 1000;
  }

  format(seconds: number): string {
    return formatElapsed(seconds);
  }
}
