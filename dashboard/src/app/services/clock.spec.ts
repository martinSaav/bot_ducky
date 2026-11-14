import { formatElapsed } from './clock';

describe('formatElapsed', () => {
  it('shows minutes and seconds under an hour', () => {
    expect(formatElapsed(754)).toBe('12:34');
    expect(formatElapsed(59.9)).toBe('0:59');
  });

  it('adds hours from one hour up', () => {
    expect(formatElapsed(4000)).toBe('1:06:40');
  });

  it('never goes negative', () => {
    expect(formatElapsed(-5)).toBe('0:00');
  });
});
