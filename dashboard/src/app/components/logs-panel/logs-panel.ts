import { AfterViewChecked, Component, computed, ElementRef, inject, OnDestroy, OnInit, signal, ViewChild } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';
import { Subscription, timer } from 'rxjs';
import { BotApiService } from '../../services/bot-api';

type Level = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR';
type Filter = 'all' | 'warnings' | 'errors';

interface LogEntry {
  time: string;
  level: Level;
  tag: string;
  text: string;
}

// "2026-11-11 21:34:56 WARNING [agent] message" (bot and agent use the same format)
const LINE = /^(?:\d{4}-\d{2}-\d{2} )?(\d{2}:\d{2}:\d{2})\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+\[([^\]]+)\]\s?(.*)$/;

/** Bot and agent logs with level filter, search and pause. */
@Component({
  selector: 'app-logs-panel',
  standalone: true,
  imports: [MatIconModule],
  template: `
    <section class="panel">
      <header class="card-head">
        <mat-icon>terminal</mat-icon>
        <h2>Logs</h2>
        <span class="card-meta">
          @if (counts().ERROR; as n) { <span class="count error">{{ n }} {{ n === 1 ? 'error' : 'errors' }}</span> }
          @if (counts().WARNING; as n) { <span class="count warn">{{ n }} {{ n === 1 ? 'warning' : 'warnings' }}</span> }
          last {{ entries().length }} lines of {{ source() === 'agent' ? 'agent.log' : 'bot.log' }}
        </span>
      </header>

      <div class="toolbar">
        <div class="segmented">
          <button [class.on]="source() === 'bot'" (click)="selectSource('bot')">Bot</button>
          <button [class.on]="source() === 'agent'" (click)="selectSource('agent')">Desktop agent</button>
        </div>
        <div class="segmented">
          <button [class.on]="filter() === 'all'" (click)="filter.set('all')">All</button>
          <button [class.on]="filter() === 'warnings'" (click)="filter.set('warnings')">Warnings+</button>
          <button [class.on]="filter() === 'errors'" (click)="filter.set('errors')">Errors</button>
        </div>
        <label class="search">
          <mat-icon>search</mat-icon>
          <input type="search" placeholder="Search logs" [value]="query()" (input)="query.set($any($event.target).value)">
        </label>
        <button class="pause" [class.on]="paused()" (click)="paused.set(!paused())"
                [attr.aria-pressed]="paused()" [title]="paused() ? 'Resume live updates' : 'Pause live updates'">
          <mat-icon>{{ paused() ? 'play_arrow' : 'pause' }}</mat-icon>{{ paused() ? 'Paused' : 'Live' }}
        </button>
      </div>

      <div class="log" #logEl>
        @for (e of visible(); track $index) {
          <div class="line" [class]="e.level.toLowerCase()">
            <span class="time">{{ e.time }}</span>
            <span class="level">{{ e.level === 'WARNING' ? 'WARN' : e.level }}</span>
            <span class="tag">{{ e.tag }}</span>
            <span class="text">{{ e.text }}</span>
          </div>
        } @empty {
          <p class="empty">{{ entries().length ? 'No lines match the filter.' : 'No logs available.' }}</p>
        }
      </div>
    </section>
  `,
  styles: [`
    .count { padding: 1px 8px; border-radius: 10px; margin-right: 6px; font-weight: 600; }
    .count.error { background: rgba(248, 113, 113, 0.15); color: var(--err); }
    .count.warn { background: rgba(251, 191, 36, 0.12); color: var(--warn); }
    .toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 10px; }
    .segmented { display: inline-flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
    .segmented button, .pause {
      background: transparent; border: none; color: var(--text-3); font: inherit; font-size: 12px;
      padding: 6px 12px; cursor: pointer;
    }
    .segmented button + button { border-left: 1px solid var(--border); }
    .segmented button.on { background: var(--accent-bg); color: var(--text-1); }
    .search { display: inline-flex; align-items: center; gap: 6px; flex: 1; min-width: 160px;
      border: 1px solid var(--border); border-radius: 8px; padding: 0 10px; }
    .search mat-icon { font-size: 16px; width: 16px; height: 16px; color: var(--text-4); }
    .search input { flex: 1; background: transparent; border: none; outline: none; color: var(--text-1);
      font: inherit; font-size: 12px; padding: 6px 0; }
    .pause { display: inline-flex; align-items: center; gap: 4px; border: 1px solid var(--border); border-radius: 8px; }
    .pause mat-icon { font-size: 16px; width: 16px; height: 16px; }
    .pause.on { color: var(--warn); border-color: rgba(251, 191, 36, 0.35); }
    .segmented button:focus-visible, .pause:focus-visible, .search:focus-within { outline: 2px solid var(--accent); outline-offset: 1px; }

    .log { max-height: 380px; overflow-y: auto; font-family: 'Roboto Mono', monospace; font-size: 11.5px;
      line-height: 1.55; border: 1px solid var(--border); border-radius: 10px; background: rgba(0,0,0,0.25); padding: 6px 0; }
    .line { display: grid; grid-template-columns: 62px 52px minmax(70px, auto) 1fr; gap: 8px; padding: 1px 10px; }
    .line:hover { background: rgba(255,255,255,0.03); }
    .time { color: var(--text-4); }
    .level { font-weight: 700; font-size: 10px; letter-spacing: 0.04em; align-self: center; }
    .tag { color: var(--accent-soft); }
    .text { color: var(--text-2); white-space: pre-wrap; word-break: break-word; }
    .info .level { color: var(--ok); }
    .debug .level { color: var(--text-4); }
    .warning .level, .warning .text { color: var(--warn); }
    .error .level, .error .text { color: var(--err); }
    .error { background: rgba(248, 113, 113, 0.06); }
    .empty { color: var(--text-4); text-align: center; font-size: 12px; padding: 16px 0; margin: 0; }
    @media (max-width: 700px) {
      .line { grid-template-columns: 58px 44px 1fr; }
      .tag { display: none; }
    }
  `]
})
export class LogsPanelComponent implements OnInit, OnDestroy, AfterViewChecked {
  @ViewChild('logEl') logEl?: ElementRef<HTMLDivElement>;
  private api = inject(BotApiService);
  private refresh?: Subscription;
  private scrollPending = false;

  source = signal<'bot' | 'agent'>('bot');
  filter = signal<Filter>('all');
  query = signal('');
  paused = signal(false);
  private lines = signal<string[]>([]);

  entries = computed(() => parse(this.lines()));
  counts = computed(() => {
    const counts: Record<Level, number> = { DEBUG: 0, INFO: 0, WARNING: 0, ERROR: 0 };
    for (const e of this.entries()) counts[e.level]++;
    return counts;
  });
  visible = computed(() => {
    const filter = this.filter();
    const query = this.query().trim().toLowerCase();
    return this.entries().filter(e =>
      (filter === 'all' || e.level === 'ERROR' || (filter === 'warnings' && e.level === 'WARNING'))
      && (!query || `${e.tag} ${e.text}`.toLowerCase().includes(query)));
  });

  ngOnInit(): void {
    this.refresh = timer(0, 10000).subscribe(() => {
      if (!this.paused()) this.load();
    });
  }

  ngOnDestroy(): void {
    this.refresh?.unsubscribe();
  }

  ngAfterViewChecked(): void {
    if (this.scrollPending && this.logEl) {
      const el = this.logEl.nativeElement;
      el.scrollTop = el.scrollHeight;
      this.scrollPending = false;
    }
  }

  selectSource(source: 'bot' | 'agent'): void {
    this.source.set(source);
    this.load();
  }

  private load(): void {
    const source = this.source();
    this.api.getLogs(200, source).subscribe(res => {
      if (this.source() !== source) return;
      this.scrollPending = res.lines.length !== this.lines().length
        || res.lines[res.lines.length - 1] !== this.lines()[this.lines().length - 1];
      this.lines.set(res.lines);
    });
  }
}

/** Log lines to entries; lines without a header (tracebacks) join the previous entry. */
function parse(lines: string[]): LogEntry[] {
  const entries: LogEntry[] = [];
  for (const line of lines) {
    const m = LINE.exec(line);
    if (m) {
      const level = (m[2] === 'CRITICAL' ? 'ERROR' : m[2]) as Level;
      entries.push({ time: m[1], level, tag: m[3], text: m[4] });
    } else if (entries.length && line.trim()) {
      entries[entries.length - 1].text += '\n' + line;
    } else if (line.trim()) {
      entries.push({ time: '', level: 'INFO', tag: '', text: line });
    }
  }
  return entries;
}
