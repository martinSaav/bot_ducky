import { Component, inject } from '@angular/core';
import { AsyncPipe } from '@angular/common';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { BotApiService } from '../../services/bot-api';
import { Subsystems } from '../../models/bot-status';

interface SubsystemDef {
  key: keyof Subsystems;
  label: string;
  icon: string;
}

@Component({
  selector: 'app-subsystems-card',
  standalone: true,
  imports: [AsyncPipe, MatCardModule, MatIconModule],
  template: `
    @if (api.status$ | async; as s) {
      <mat-card class="dash-card">
        <mat-card-header>
          <mat-icon mat-card-avatar class="card-icon">settings_input_component</mat-icon>
          <mat-card-title>Subsistemas</mat-card-title>
          <mat-card-subtitle>Estado de cada módulo</mat-card-subtitle>
        </mat-card-header>
        <mat-card-content>
          <div class="subsystems-grid">
            @for (sub of defs; track sub.key) {
              <div class="subsystem-chip" [class.active]="s.subsystems[sub.key]" [class.inactive]="!s.subsystems[sub.key]">
                <mat-icon class="sub-icon">{{ sub.icon }}</mat-icon>
                <span class="sub-label">{{ sub.label }}</span>
                <span class="sub-dot" [class.on]="s.subsystems[sub.key]" [class.off]="!s.subsystems[sub.key]"></span>
              </div>
            }
          </div>
        </mat-card-content>
      </mat-card>
    }
  `,
  styles: [`
    .subsystems-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; padding-top: 4px; }
    .subsystem-chip { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-radius: 10px; border: 1px solid transparent; transition: all 0.3s ease; }
    .subsystem-chip.active { background: rgba(167,139,250,0.1); border-color: rgba(167,139,250,0.25); }
    .subsystem-chip.inactive { background: rgba(255,255,255,0.03); border-color: rgba(255,255,255,0.08); opacity: 0.5; }
    .sub-icon { font-size: 18px; height: 18px; width: 18px; color: rgba(255,255,255,0.6); }
    .subsystem-chip.active .sub-icon { color: #a78bfa; }
    .sub-label { flex: 1; font-size: 12px; font-weight: 500; }
    .sub-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
    .sub-dot.on { background: #34d399; box-shadow: 0 0 5px #34d399; }
    .sub-dot.off { background: #4b5563; }
  `]
})
export class SubsystemsCardComponent {
  api = inject(BotApiService);

  readonly defs: SubsystemDef[] = [
    { key: 'chat_bot',        label: 'Chat bot',     icon: 'chat' },
    { key: 'agent',           label: 'Agente Rust',  icon: 'computer' },
    { key: 'discord_presence',label: 'Discord',      icon: 'discord' },
    { key: 'clips_pipeline',  label: 'Clips',        icon: 'movie' },
    { key: 'llm_summary',     label: 'LLM',          icon: 'smart_toy' },
    { key: 'database',        label: 'PostgreSQL',   icon: 'storage' },
    { key: 'embeddings',      label: 'Embeddings',   icon: 'hub' },
  ];
}
