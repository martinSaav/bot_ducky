import { lastMatchResult } from './match-card';

describe('lastMatchResult', () => {
  it('uses the LoL client result', () => {
    expect(lastMatchResult({ id: '1634801259', result: 'win' })).toEqual({ won: true, text: 'W' });
    expect(lastMatchResult({ id: '1634801259', result: 'loss' })).toEqual({ won: false, text: 'L' });
  });

  it('reads a Valorant result from the final score', () => {
    expect(lastMatchResult({ id: 'val-1', score: { ally: 13, enemy: 9 } })).toEqual({ won: true, text: 'W 13–9' });
    expect(lastMatchResult({ id: 'val-1', score: { ally: 7, enemy: 13 } })).toEqual({ won: false, text: 'L 7–13' });
  });

  it('leaves a tie undecided', () => {
    expect(lastMatchResult({ id: 'val-1', score: { ally: 12, enemy: 12 } })).toEqual({ won: null, text: '– 12–12' });
  });
});
