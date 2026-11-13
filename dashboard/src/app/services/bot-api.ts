import { inject, Injectable, signal } from '@angular/core';
import { HttpClient, HttpErrorResponse, HttpHeaders } from '@angular/common/http';
import { EMPTY, Observable, catchError, shareReplay, switchMap, tap, timer } from 'rxjs';
import { BotStatus } from '../models/bot-status';
import { dashboardToken } from './dashboard-token';

const API_BASE = '/api';

/** Why the latest status request failed; null while it works. */
export type ApiProblem = 'unreachable' | 'unauthorized' | null;

@Injectable({ providedIn: 'root' })
export class BotApiService {
  private http = inject(HttpClient);
  private headers = authHeaders();

  /** Epoch ms of the last successful status; null until the first one. */
  readonly lastUpdate = signal<number | null>(null);
  readonly problem = signal<ApiProblem>(null);

  /**
   * Full bot status, refreshed every 5 seconds. A failed request does not end
   * the stream: the last good status stays on screen and polling continues,
   * so the dashboard recovers on its own when the bot comes back.
   */
  readonly status$: Observable<BotStatus> = timer(0, 5000).pipe(
    switchMap(() => this.http.get<BotStatus>(`${API_BASE}/status`, { headers: this.headers }).pipe(
      tap(() => {
        this.problem.set(null);
        this.lastUpdate.set(Date.now());
      }),
      catchError((err: HttpErrorResponse) => {
        this.problem.set(err.status === 401 ? 'unauthorized' : 'unreachable');
        return EMPTY;
      }),
    )),
    shareReplay({ bufferSize: 1, refCount: true }),
  );

  /** Last N lines of the bot's or the agent's log. */
  getLogs(n = 100, source: 'bot' | 'agent' = 'bot'): Observable<{ lines: string[] }> {
    return this.http.get<{ lines: string[] }>(`${API_BASE}/logs`, {
      params: { n: String(n), source },
      headers: this.headers
    });
  }
}

function authHeaders(): HttpHeaders {
  const token = dashboardToken();
  return token ? new HttpHeaders({ Authorization: `Bearer ${token}` }) : new HttpHeaders();
}
