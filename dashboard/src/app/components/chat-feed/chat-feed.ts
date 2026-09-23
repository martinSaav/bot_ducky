import { Component, inject, OnInit, signal, ElementRef, ViewChild, AfterViewChecked } from '@angular/core';
import { DatePipe } from '@angular/common';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { WsService } from '../../services/ws';
import { BotApiService } from '../../services/bot-api';
import { ChatMessage } from '../../models/bot-status';

const MAX_MESSAGES = 100;

@Component({
  selector: 'app-chat-feed',
  standalone: true,
  imports: [DatePipe, MatCardModule, MatIconModule],
  template: `
    <mat-card class="dash-card chat-card">
      <mat-card-header>
        <mat-icon mat-card-avatar class="card-icon">forum</mat-icon>
        <mat-card-title>Chat en vivo</mat-card-title>
        <mat-card-subtitle>{{ messages().length }} mensajes</mat-card-subtitle>
      </mat-card-header>
      <mat-card-content>
        <div class="chat-feed" #feedEl>
          @for (msg of messages(); track msg.ts) {
            <div class="chat-msg">
              <span class="msg-author">{{ msg.author }}</span>
              <span class="msg-text">{{ msg.text }}</span>
              <span class="msg-time">{{ msg.ts * 1000 | date:'HH:mm:ss' }}</span>
            </div>
          } @empty {
            <p class="empty-chat">Esperando mensajes del chat…</p>
          }
        </div>
      </mat-card-content>
    </mat-card>
  `,
  styles: [`
    .chat-card { height: 100%; display: flex; flex-direction: column; }
    .chat-feed { max-height: 320px; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; padding-right: 4px; }
    .chat-feed::-webkit-scrollbar { width: 4px; }
    .chat-feed::-webkit-scrollbar-thumb { background: rgba(167,139,250,0.3); border-radius: 2px; }
    .chat-msg { display: flex; align-items: baseline; gap: 8px; padding: 4px 6px; border-radius: 6px; transition: background 0.2s; font-size: 13px; }
    .chat-msg:hover { background: rgba(255,255,255,0.04); }
    .msg-author { color: #a78bfa; font-weight: 700; flex-shrink: 0; }
    .msg-text { flex: 1; color: rgba(255,255,255,0.85); word-break: break-word; }
    .msg-time { color: rgba(255,255,255,0.25); font-size: 10px; font-family: monospace; flex-shrink: 0; }
    .empty-chat { color: rgba(255,255,255,0.3); font-size: 13px; padding: 16px 0; text-align: center; }
  `]
})
export class ChatFeedComponent implements OnInit, AfterViewChecked {
  @ViewChild('feedEl') feedEl!: ElementRef<HTMLDivElement>;

  private ws = inject(WsService);
  private api = inject(BotApiService);

  messages = signal<ChatMessage[]>([]);
  private shouldScroll = false;

  ngOnInit(): void {
    // Seed with recent messages from REST snapshot
    this.api.status$.subscribe(s => {
      if (this.messages().length === 0 && s.chat_recent?.length) {
        this.messages.set([...s.chat_recent]);
      }
    });

    // Push real-time messages from WebSocket
    this.ws.chat$.subscribe(msg => {
      this.messages.update(msgs => {
        const updated = [...msgs, msg];
        return updated.length > MAX_MESSAGES ? updated.slice(-MAX_MESSAGES) : updated;
      });
      this.shouldScroll = true;
    });
  }

  ngAfterViewChecked(): void {
    if (this.shouldScroll && this.feedEl) {
      const el = this.feedEl.nativeElement;
      el.scrollTop = el.scrollHeight;
      this.shouldScroll = false;
    }
  }
}
