import { Component, inject } from '@angular/core';
import { AsyncPipe } from '@angular/common';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { BotApiService } from '../../services/bot-api';

@Component({
  selector: 'app-riot-card',
  standalone: true,
  imports: [AsyncPipe, MatCardModule, MatIconModule],
  template: `
    @if (api.status$ | async; as s) {
      <mat-card class="dash-card">
        <mat-card-header>
          <mat-icon mat-card-avatar class="card-icon">shield</mat-icon>
          <mat-card-title>Riot</mat-card-title>
          <mat-card-subtitle>League of Legends & VALORANT</mat-card-subtitle>
        </mat-card-header>
        <mat-card-content>
          <!-- LoL -->
          <div class="game-section">
            <div class="game-header">
              <span class="game-label lol">League of Legends</span>
              <span class="status-pill"
                [class.ok]="s.riot.lol.configured && !s.riot.lol.key_dead"
                [class.warn]="!s.riot.lol.configured || s.riot.lol.key_dead">
                @if (s.riot.lol.key_dead) { Key expirada }
                @else if (s.riot.lol.configured) { Configurado }
                @else { No configurado }
              </span>
            </div>
            @if (s.riot.lol.key_dead) {
              <p class="hint">
                <mat-icon class="hint-icon">warning</mat-icon>
                La API key de Riot expiró. Renovar en developer.riotgames.com
              </p>
            }
          </div>

          <div class="divider"></div>

          <!-- Valorant -->
          <div class="game-section">
            <div class="game-header">
              <span class="game-label val">VALORANT</span>
              <span class="status-pill"
                [class.ok]="s.riot.valorant.configured"
                [class.warn]="!s.riot.valorant.configured">
                {{ s.riot.valorant.configured ? 'Configurado (HenrikDev)' : 'No configurado' }}
              </span>
            </div>
          </div>
        </mat-card-content>
      </mat-card>
    }
  `,
  styles: [`
    .game-section { padding: 10px 0; }
    .game-header { display: flex; justify-content: space-between; align-items: center; }
    .game-label { font-weight: 600; font-size: 13px; }
    .game-label.lol { color: #c8aa6e; }
    .game-label.val { color: #ff4655; }
    .status-pill { padding: 2px 10px; border-radius: 12px; font-size: 11px; font-weight: 600; }
    .status-pill.ok { background: rgba(52,211,153,0.15); color: #34d399; border: 1px solid rgba(52,211,153,0.3); }
    .status-pill.warn { background: rgba(251,191,36,0.15); color: #fbbf24; border: 1px solid rgba(251,191,36,0.3); }
    .hint { display: flex; align-items: center; gap: 4px; font-size: 11px; color: #fbbf24; margin: 6px 0 0; }
    .hint-icon { font-size: 14px; height: 14px; width: 14px; }
    .divider { height: 1px; background: rgba(255,255,255,0.06); margin: 2px 0; }
  `]
})
export class RiotCardComponent {
  api = inject(BotApiService);
}
