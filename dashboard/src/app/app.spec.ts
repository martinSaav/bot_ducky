import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { AppComponent } from './app';
import { statusFixture } from './testing/status.fixture';

describe('AppComponent', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AppComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
  });

  it('renders the brand before any data arrives', () => {
    const fixture = TestBed.createComponent(AppComponent);
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).querySelector('.brand-title')?.textContent).toContain('BotDucky');
  });

  it('shows the stream as live with its title once the status arrives', async () => {
    const fixture = TestBed.createComponent(AppComponent);
    const http = TestBed.inject(HttpTestingController);
    fixture.detectChanges();
    await new Promise(resolve => setTimeout(resolve, 20));  // the status poll starts on a timer

    for (const request of http.match('/api/status')) {
      request.flush(statusFixture({ stream: { live: true, title: 'ranked hasta platino', viewers: 42, started_at: null, game: 'VALORANT' } }));
    }
    fixture.detectChanges();

    const header = (fixture.nativeElement as HTMLElement).querySelector('.topbar')!.textContent!;
    expect(header).toContain('LIVE');
    expect(header).toContain('ranked hasta platino');
  });

  it('shows a banner when the bot does not answer', async () => {
    const fixture = TestBed.createComponent(AppComponent);
    const http = TestBed.inject(HttpTestingController);
    fixture.detectChanges();
    await new Promise(resolve => setTimeout(resolve, 20));

    for (const request of http.match('/api/status')) {
      request.flush('down', { status: 503, statusText: 'Service Unavailable' });
    }
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('.banner')?.textContent).toContain('Bot unreachable');
  });
});
