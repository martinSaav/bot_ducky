import { Component, inject } from '@angular/core';
import { AsyncPipe } from '@angular/common';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { BotApiService } from '../../services/bot-api';
import { ClockService, formatElapsed } from '../../services/clock';
import { LastMatch, MatchStatus } from '../../models/bot-status';

interface Step {
  phase: string;
  label: string;
}

/** Phases the agent reports, in order. Valorant's client has no post-game phase. */
const STEPS: Record<'lol' | 'valorant', Step[]> = {
  valorant: [
    { phase: 'menus', label: 'Menus' },
    { phase: 'pregame', label: 'Agent select' },
    { phase: 'in_game', label: 'In game' },
  ],
  lol: [
    { phase: 'menus', label: 'Lobby' },
    { phase: 'pregame', label: 'Champ select' },
    { phase: 'in_game', label: 'In game' },
    { phase: 'post_game', label: 'Post-game' },
  ],
};

@Component({
  selector: 'app-match-card',
  standalone: true,
  imports: [AsyncPipe, MatCardModule, MatIconModule],
  template: `
    @if (api.status$ | async; as s) {
      <mat-card class="dash-card match-card" [class.lol]="kind(s.match) === 'lol'" [class.valorant]="kind(s.match) === 'valorant'">
        @if (s.match; as m) {
          <div class="match-layout">
            <div class="match-main">
              <div class="match-head">
                <span class="game">{{ m.game }}</span>
                @if (m.queue) {
                  <span class="queue">{{ m.queue }}</span>
                }
                @if (m.since && m.phase !== 'menus') {
                  <span class="timer" title="Time since the match started (as seen by the bot)">
                    <mat-icon>timer</mat-icon>{{ elapsed(m.since) }}
                  </span>
                }
              </div>

              <ol class="steps">
                @for (step of steps(m); track step.phase; let i = $index) {
                  <li [class.done]="i < stepIndex(m)" [class.current]="i === stepIndex(m)">
                    <span class="dot"></span>{{ step.label }}
                  </li>
                }
              </ol>
            </div>

            @if (m.score && m.phase === 'in_game') {
              <div class="score" [class.ahead]="m.score.ally > m.score.enemy" [class.behind]="m.score.ally < m.score.enemy">
                <div class="side">
                  <span class="num">{{ m.score.ally }}</span>
                  <span class="who">Ally</span>
                </div>
                <span class="sep">–</span>
                <div class="side">
                  <span class="num">{{ m.score.enemy }}</span>
                  <span class="who">Enemy</span>
                </div>
                <span class="round">Round {{ m.score.ally + m.score.enemy + 1 }}</span>
              </div>
            } @else if (m.phase !== 'in_game' && m.last_match; as last) {
              <div class="last" [class.win]="won(last) === true" [class.loss]="won(last) === false">
                <span class="last-label">Last match</span>
                <span class="last-result">{{ resultText(last) }}</span>
              </div>
            }
          </div>
        } @else {
          <div class="no-match">
            <mat-icon>sports_esports</mat-icon>
            <div>
              <div class="no-match-title">No match data</div>
              <div class="no-match-sub">
                @if (s.current_game) {
                  {{ s.current_game }} — the agent only reads matches for League of Legends and VALORANT.
                } @else {
                  No game running, or the agent is not reporting.
                }
              </div>
            </div>
          </div>
        }
      </mat-card>
    }
  `,
  styles: [`
    :host { display: block; }
    .match-card { padding: 20px 24px; --accent: #a78bfa; border-left: 3px solid var(--accent) !important; }
    .match-card.lol { --accent: #c8aa6e; }
    .match-card.valorant { --accent: #ff4655; }
    .match-layout { display: flex; align-items: center; justify-content: space-between; gap: 24px; flex-wrap: wrap; }
    .match-main { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
    .match-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
    .game { font-size: 22px; font-weight: 700; letter-spacing: -0.02em; color: var(--accent); }
    .queue { font-size: 12px; font-weight: 600; padding: 3px 10px; border-radius: 12px; background: rgba(255,255,255,0.07); color: rgba(255,255,255,0.75); }
    .timer { display: inline-flex; align-items: center; gap: 4px; font-family: 'Roboto Mono', monospace; font-size: 14px; color: rgba(255,255,255,0.8); }
    .timer mat-icon { font-size: 16px; width: 16px; height: 16px; color: rgba(255,255,255,0.45); }

    .steps { display: flex; gap: 0; list-style: none; margin: 0; padding: 0; flex-wrap: wrap; }
    .steps li { display: flex; align-items: center; gap: 6px; font-size: 12px; color: rgba(255,255,255,0.35); padding-right: 18px; position: relative; }
    .steps li:not(:last-child)::after { content: ''; width: 10px; height: 1px; background: rgba(255,255,255,0.15); position: absolute; right: 4px; top: 50%; }
    .steps .dot { width: 8px; height: 8px; border-radius: 50%; background: rgba(255,255,255,0.15); }
    .steps li.done { color: rgba(255,255,255,0.55); }
    .steps li.done .dot { background: rgba(255,255,255,0.45); }
    .steps li.current { color: #fff; font-weight: 600; }
    .steps li.current .dot { background: var(--accent); box-shadow: 0 0 8px var(--accent); }

    .score { display: grid; grid-template-columns: auto auto auto; align-items: center; column-gap: 14px; text-align: center; }
    .score .side { display: flex; flex-direction: column; }
    .score .num { font-size: 44px; font-weight: 800; line-height: 1; font-variant-numeric: tabular-nums; }
    .score .who { font-size: 10px; text-transform: uppercase; letter-spacing: 0.1em; color: rgba(255,255,255,0.4); margin-top: 4px; }
    .score .sep { font-size: 28px; color: rgba(255,255,255,0.3); }
    .score .round { grid-column: 1 / -1; font-size: 11px; color: rgba(255,255,255,0.45); margin-top: 6px; }
    .score.ahead .side:first-child .num { color: #34d399; }
    .score.behind .side:last-child .num { color: #f87171; }

    .last { display: flex; flex-direction: column; align-items: flex-end; gap: 2px; }
    .last-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.1em; color: rgba(255,255,255,0.4); }
    .last-result { font-size: 22px; font-weight: 700; color: rgba(255,255,255,0.7); }
    .last.win .last-result { color: #34d399; }
    .last.loss .last-result { color: #f87171; }

    .no-match { display: flex; align-items: center; gap: 14px; color: rgba(255,255,255,0.5); }
    .no-match mat-icon { font-size: 28px; width: 28px; height: 28px; color: rgba(255,255,255,0.3); }
    .no-match-title { font-size: 15px; font-weight: 600; color: rgba(255,255,255,0.75); }
    .no-match-sub { font-size: 12px; margin-top: 2px; }
  `]
})
export class MatchCardComponent {
  api = inject(BotApiService);
  private clock = inject(ClockService);

  kind(m: MatchStatus | null | undefined): 'lol' | 'valorant' | null {
    const game = (m?.game ?? '').toLowerCase();
    if (game.includes('league')) return 'lol';
    if (game.includes('valorant')) return 'valorant';
    return null;
  }

  steps(m: MatchStatus): Step[] {
    return STEPS[this.kind(m) ?? 'valorant'];
  }

  stepIndex(m: MatchStatus): number {
    return this.steps(m).findIndex(step => step.phase === m.phase);
  }

  elapsed(since: number): string {
    return formatElapsed(this.clock.now() / 1000 - since);
  }

  /** true = win, false = loss, null = unknown (tie, no result yet). */
  won(last: LastMatch): boolean | null {
    if (last.result === 'win') return true;
    if (last.result === 'loss') return false;
    if (last.score && last.score.ally !== last.score.enemy) return last.score.ally > last.score.enemy;
    return null;
  }

  resultText(last: LastMatch): string {
    const won = this.won(last);
    const letter = won === true ? 'W' : won === false ? 'L' : '–';
    return last.score ? `${letter} ${last.score.ally}–${last.score.enemy}` : letter;
  }
}
