import { describe, expect, it } from 'vitest';

import { CATALOG, manifest, rawCandlesSummary } from '@/test/api';

import {
  columnsOf,
  formatParams,
  formatReads,
  formatWarmUp,
  groupsOf,
  representationName,
  rowsDrawn,
  ROWS_DRAWN,
  shadesOf,
} from './manifest';

describe('representationName', () => {
  it('names the rows short on a tile and long in the upload, a block it does not know by its id', () => {
    expect(representationName('candles@1')).toBe('Candles');
    expect(representationName('renko-triples@1')).toBe('Renko');
    expect(representationName('renko-triples@1', true)).toBe('Renko bricks');
    expect(representationName('renko-triples@2')).toBe('Renko');
    expect(representationName('heikin-ashi@1')).toBe('heikin-ashi@1');
  });
});

describe('what a manifest reads', () => {
  it('writes the representation with the timeframe, and the warm-up exact or estimated', () => {
    expect(formatReads(rawCandlesSummary())).toBe('Candles · 30m');
    expect(formatWarmUp(rawCandlesSummary())).toBe('24 candles');
    expect(formatWarmUp(rawCandlesSummary(2))).toBe('1 candle');
    const renko = { ...rawCandlesSummary(), warm_up: { candles: 1200, kind: 'estimate' as const } };
    expect(formatWarmUp(renko)).toBe('≈ 1,200 candles');
  });

  it('writes the parameters of a block, none without any', () => {
    expect(formatParams({ window: 20, dev: 2 })).toBe('window 20, dev 2');
    expect(formatParams({ atr_multiple: 0.6 })).toBe('atr multiple 0.6');
    expect(formatParams({})).toBe('none');
    expect(formatParams(undefined)).toBe('none');
  });
});

describe('columnsOf', () => {
  it('describes each column by its block in the catalog, in the order of the manifest', () => {
    expect(columnsOf(manifest(), CATALOG)[0]).toEqual({
      name: 'open',
      group: 'Candle',
      description: 'The open of the candle',
      settings: 'none',
      scaling: 'as is',
    });
    expect(columnsOf(manifest(), CATALOG).map((column) => column.name)).toEqual([
      'open',
      'high',
      'low',
      'close',
      'volume',
    ]);
  });

  it('names the scaling of the BLESK models, and a column of a block the catalog does not list by its id', () => {
    const blesk = manifest(25, {});
    blesk.input.features = [
      { name: 'rsi', id: 'rsi@1', params: { window: 20 }, norm: { id: 'blesk-norm@1' } },
      { name: 'direction', id: 'renko-direction@1' },
    ];
    expect(columnsOf(blesk, CATALOG)).toEqual([
      { name: 'rsi', group: 'Columns', description: 'rsi@1', settings: 'window 20', scaling: 'BLESK scaling' },
      { name: 'direction', group: 'Columns', description: 'renko-direction@1', settings: 'none', scaling: 'as is' },
    ]);
  });

  it('waits for the catalog with the plain ids', () => {
    expect(columnsOf(manifest(), undefined)[0]?.description).toBe('open@1');
  });
});

describe('groupsOf', () => {
  it('spans a group over the columns next to each other that share it', () => {
    const column = (group: string) => ({ name: group, group, description: '', settings: '', scaling: '' });
    const columns = ['Bands', 'Bands', 'Momentum', 'Bands'].map(column);
    expect(groupsOf(columns)).toEqual([
      { group: 'Bands', span: 2 },
      { group: 'Momentum', span: 1 },
      { group: 'Bands', span: 1 },
    ]);
  });
});

describe('rowsDrawn', () => {
  it('draws every row of a short window and spreads the rows of a long one, both ends included', () => {
    expect(rowsDrawn(3)).toEqual([0, 1, 2]);
    const long = rowsDrawn(1000);
    expect(long).toHaveLength(ROWS_DRAWN);
    expect([long[0], long.at(-1)]).toEqual([0, 999]);
  });
});

describe('shadesOf', () => {
  it('shades each column of the first sample from its lowest to its highest value', () => {
    const example = manifest(3, {
      example: {
        input: [
          [
            [1, 5, 0, 0, 7],
            [2, 5, 0, 0, 7],
            [3, 5, 0, 0, 7],
          ],
        ],
        output: [[0.1]],
      },
    });
    const shades = shadesOf(example, [0, 1, 2]);
    expect(shades?.[0]).toEqual([12, 43, 74]);
    // A column of one value has no lowest or highest: the middle shade.
    expect(shades?.[1]).toEqual([43, 43, 43]);
  });

  it('has nothing to shade by without an example', () => {
    expect(shadesOf(manifest(), [0, 1])).toBeNull();
  });
});
