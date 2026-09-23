import { Component, inject } from '@angular/core';
import { AsyncPipe, NgClass, KeyValuePipe, TitleCasePipe } from '@angular/common';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatChipsModule } from '@angular/material/chips';
import { BotApiService } from '../../services/bot-api';

@Component({
  selector: 'app-prediction-card',
  standalone: true,
  imports: [AsyncPipe, KeyValuePipe, TitleCasePipe, MatCardModule, MatIconModule, MatChipsModule],
  template: `
    @if (api.status$ | async; as s) {
      <mat-card class="dash-card" [class.active-pred]="s.prediction">
        <mat-card-header>
          <mat-icon mat-card-avatar class="card-icon">currency_exchange</mat-icon>
          <mat-card-title>Prediction</mat-card-title>
          <mat-card-subtitle>
            @if (s.prediction) { Activa } @else { Sin prediction activa }
          </mat-card-subtitle>
        </mat-card-header>
        <mat-card-content>
          @if (s.prediction; as pred) {
            <div class="pred-game">
              <mat-icon class="small-icon">sports_esports</mat-icon>
              <span>{{ pred.game }}</span>
            </div>
            <div class="stat-row">
              <span class="label">Prediction ID</span>
              <span class="value mono small">{{ pred.prediction_id }}</span>
            </div>
            <div class="stat-row">
              <span class="label">Match key</span>
              <span class="value mono small">{{ pred.match_key }}</span>
            </div>
            <div class="outcomes-title">Opciones</div>
            <div class="outcomes">
              @for (entry of pred.outcomes | keyvalue; track entry.key) {
                <div class="outcome-chip">
                  <span class="outcome-name">{{ entry.key | titlecase }}</span>
                  <span class="outcome-id mono">{{ entry.value }}</span>
                </div>
              }
            </div>
          } @else {
            <div class="no-pred">
              <mat-icon>radio_button_unchecked</mat-icon>
              <span>No hay ninguna prediction activa registrada</span>
            </div>
          }
        </mat-card-content>
      </mat-card>
    }
  `,
  styles: [`
    .active-pred { border-color: rgba(167,139,250,0.4) !important; }
    .pred-game { display: flex; align-items: center; gap: 6px; color: #c084fc; font-weight: 600; font-size: 16px; margin-bottom: 12px; }
    .small-icon { font-size: 18px; height: 18px; width: 18px; }
    .stat-row { display: flex; justify-content: space-between; align-items: center; padding: 5px 0; border-bottom: 1px solid rgba(255,255,255,0.06); }
    .label { color: rgba(255,255,255,0.5); font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; }
    .value { font-size: 12px; }
    .mono { font-family: monospace; }
    .small { font-size: 11px; color: rgba(255,255,255,0.6); }
    .outcomes-title { margin-top: 14px; margin-bottom: 8px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: rgba(255,255,255,0.4); }
    .outcomes { display: flex; gap: 8px; flex-wrap: wrap; }
    .outcome-chip { background: rgba(167,139,250,0.1); border: 1px solid rgba(167,139,250,0.3); border-radius: 8px; padding: 6px 12px; display: flex; flex-direction: column; gap: 2px; }
    .outcome-name { font-size: 13px; font-weight: 600; color: #c084fc; }
    .outcome-id { font-size: 10px; color: rgba(255,255,255,0.4); }
    .no-pred { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 16px 0; color: rgba(255,255,255,0.3); }
    .no-pred mat-icon { font-size: 36px; height: 36px; width: 36px; }
  `]
})
export class PredictionCardComponent {
  api = inject(BotApiService);
}
