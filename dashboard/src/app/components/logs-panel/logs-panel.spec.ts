import { parseLogs } from './logs-panel';

describe('parseLogs', () => {
  it('splits a log line into time, level, tag and text', () => {
    expect(parseLogs(['2026-11-12 22:43:10 WARNING [riot.lol] Riot API key invalid or expired (HTTP 401).'])).toEqual([
      { time: '22:43:10', level: 'WARNING', tag: 'riot.lol', text: 'Riot API key invalid or expired (HTTP 401).' },
    ]);
  });

  it('treats CRITICAL as an error', () => {
    const [entry] = parseLogs(['2026-11-14 02:00:00 CRITICAL [watchdog] Event loop unresponsive for 61s']);
    expect(entry.level).toBe('ERROR');
  });

  it('keeps a traceback with the error line it belongs to', () => {
    const entries = parseLogs([
      '2026-11-12 22:44:00 ERROR   [chat_qa] Failed to answer !pregunta',
      'Traceback (most recent call last):',
      '  File "/app/src/chat_qa.py", line 50, in answer',
      'RuntimeError: boom',
      '2026-11-12 22:45:12 INFO    [run] Message saved from abuel077',
    ]);
    expect(entries.length).toBe(2);
    expect(entries[0].text).toContain('RuntimeError: boom');
    expect(entries[1].tag).toBe('run');
  });

  it('skips blank lines and keeps a stray first line as INFO', () => {
    const entries = parseLogs(['', 'something without a header', '   ']);
    expect(entries).toEqual([{ time: '', level: 'INFO', tag: '', text: 'something without a header' }]);
  });
});
