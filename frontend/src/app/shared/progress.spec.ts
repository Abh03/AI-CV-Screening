import { describe, expect, it } from 'vitest';
import { pairProgress } from './progress';

describe('pairProgress', () => {
  it('uses accepted CVs times JDs and terminal pairs', () => {
    expect(pairProgress({ cvs: 1000, jds: 4, terminal_pairs: 2200, pairs: { SUCCESS: 20, REVIEW_REQUIRED: 180, PENDING: 1800 } }))
      .toEqual({ complete: 2200, total: 4000, percent: 55 });
  });
  it('handles intake without division by zero', () => {
    expect(pairProgress({ cvs: 0, jds: 4, terminal_pairs: 0, pairs: {} })).toEqual({ complete: 0, total: 0, percent: 0 });
  });
});
