import { inject, Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, timer, switchMap, share } from 'rxjs';
import { BotStatus } from '../models/bot-status';

const API_BASE = '/api';

@Injectable({ providedIn: 'root' })
export class BotApiService {
  private http = inject(HttpClient);

  /** Estado completo del bot, actualizado cada 5 segundos. */
  readonly status$: Observable<BotStatus> = timer(0, 5000).pipe(
    switchMap(() => this.http.get<BotStatus>(`${API_BASE}/status`)),
    share()
  );

  /** Últimas N líneas del log del bot. */
  getLogs(n = 100): Observable<{ lines: string[] }> {
    return this.http.get<{ lines: string[] }>(`${API_BASE}/logs`, {
      params: { n: String(n) }
    });
  }
}
