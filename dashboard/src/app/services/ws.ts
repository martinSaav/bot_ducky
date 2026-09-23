import { Injectable, OnDestroy } from '@angular/core';
import { Observable, Subject, timer, EMPTY } from 'rxjs';
import { catchError, switchMap, tap } from 'rxjs/operators';
import { WsEvent, ChatMessage } from '../models/bot-status';

const WS_URL = `ws://${window.location.hostname}:${window.location.port || 8787}/ws`;

@Injectable({ providedIn: 'root' })
export class WsService implements OnDestroy {
  private ws: WebSocket | null = null;
  private _events$ = new Subject<WsEvent>();
  private _chat$ = new Subject<ChatMessage>();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  readonly events$: Observable<WsEvent> = this._events$.asObservable();
  readonly chat$: Observable<ChatMessage> = this._chat$.asObservable();

  constructor() {
    this.connect();
  }

  private connect(): void {
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
    }
    try {
      this.ws = new WebSocket(WS_URL);

      this.ws.onmessage = (evt) => {
        try {
          const msg: WsEvent = JSON.parse(evt.data);
          this._events$.next(msg);
          if (msg.event === 'chat') {
            this._chat$.next(msg.data as ChatMessage);
          }
        } catch {
          // ignore malformed messages
        }
      };

      this.ws.onclose = () => {
        // Reconnect after 3 seconds
        this.reconnectTimer = setTimeout(() => this.connect(), 3000);
      };

      this.ws.onerror = () => {
        this.ws?.close();
      };
    } catch {
      this.reconnectTimer = setTimeout(() => this.connect(), 5000);
    }
  }

  ngOnDestroy(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
    }
    this._events$.complete();
    this._chat$.complete();
  }
}
