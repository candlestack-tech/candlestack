import { describe, expect, it } from 'vitest';

import { applyOption, filterOptions, filtersSummary, nextActive, sectionStart } from './filterRules';

const PAGE = {
  counts: { all: 15, starred: 5, failed: 2, public: 3 },
  models: [
    { name: 'blesk-novol-8.keras', framework: 'keras', count: 9 },
    { name: 'stockmixer.keras', framework: 'keras', count: 6 },
  ],
};

describe('filterOptions', () => {
  it('lists Show with the totals, Public only among them, then All models and every model with its count', () => {
    const options = filterOptions({}, PAGE);
    expect(options.map((option) => [option.label, option.count, option.selected])).toEqual([
      ['All backtests', 15, true],
      ['Starred', 5, false],
      ['Public only', 3, false],
      ['Failed', 2, false],
      ['All models', undefined, true],
      ['blesk-novol-8.keras', 9, false],
      ['stockmixer.keras', 6, false],
    ]);
  });

  it('marks what the URL picked, the model in any case', () => {
    const options = filterOptions({ show: 'failed', model: 'StockMixer.keras' }, PAGE);
    expect(options.filter((option) => option.selected).map((option) => option.label)).toEqual([
      'Failed',
      'stockmixer.keras',
    ]);
    expect(filterOptions({ show: 'public' }, PAGE).find((option) => option.selected)?.label).toBe('Public only');
  });
});

describe('filtersSummary', () => {
  it('sums the filters up as the mockup does', () => {
    expect(filtersSummary({})).toBe('All backtests');
    expect(filtersSummary({ show: 'starred' })).toBe('Starred');
    expect(filtersSummary({ show: 'public' })).toBe('Public only');
    expect(filtersSummary({ show: 'failed', model: 'stockmixer.onnx' })).toBe('Failed · stockmixer.onnx');
    expect(filtersSummary({ model: 'blesk-novol-8.keras' })).toBe('blesk-novol-8.keras');
  });
});

describe('applyOption', () => {
  it('keeps the other filter and the search, and starts at page 1', () => {
    const [, starred, published, , all, blesk] = filterOptions({}, PAGE);
    const search = { q: 'aapl', model: 'x.keras', sort: 'sharpe', order: 'asc', page: 2 } as const;
    expect(applyOption(search, starred!)).toEqual({ ...search, show: 'starred', page: undefined });
    expect(applyOption(search, published!)).toEqual({ ...search, show: 'public', page: undefined });
    expect(applyOption({ show: 'failed', page: 3 }, blesk!)).toEqual({
      show: 'failed',
      model: 'blesk-novol-8.keras',
      page: undefined,
    });
    expect(applyOption(search, all!)).toEqual({ ...search, model: undefined, page: undefined });
  });
});

describe('nextActive', () => {
  const options = filterOptions({ model: 'stockmixer.keras' }, PAGE);

  it('starts each section on its chosen entry', () => {
    expect(sectionStart(options, 'show')).toBe(0);
    expect(sectionStart(options, 'model')).toBe(6);
  });

  it('moves in a section with the arrows, Home and End, stopping at the ends', () => {
    expect(nextActive(options, 0, 'ArrowDown')).toBe(1);
    expect(nextActive(options, 3, 'ArrowDown')).toBe(3);
    expect(nextActive(options, 4, 'ArrowUp')).toBe(4);
    expect(nextActive(options, 4, 'End')).toBe(6);
    expect(nextActive(options, 6, 'Home')).toBe(4);
  });

  it('goes across with ArrowLeft and ArrowRight', () => {
    expect(nextActive(options, 1, 'ArrowRight')).toBe(6);
    expect(nextActive(options, 5, 'ArrowLeft')).toBe(0);
    expect(nextActive(options, 1, 'ArrowLeft')).toBe(1);
  });

  it('goes to the next entry with the letter typed', () => {
    expect(nextActive(options, 0, 's')).toBe(1);
    expect(nextActive(options, 1, 's')).toBe(6);
    expect(nextActive(options, 0, 'p')).toBe(2);
    expect(nextActive(options, 0, 'z')).toBeUndefined();
    expect(nextActive(options, 0, 'Shift')).toBeUndefined();
  });
});
