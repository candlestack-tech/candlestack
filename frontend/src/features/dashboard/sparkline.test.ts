import { describe, expect, it } from 'vitest';

import { sparkPaths } from './sparkline';

describe('sparkPaths', () => {
  it('draws both lines on one scale, 2 px from the edges', () => {
    expect(sparkPaths([0, 0.5, 1], [0, 0.25, 0.5], 52, 20)).toEqual({
      strategy: 'M0.0 18.0L26.0 10.0L52.0 2.0',
      benchmark: 'M0.0 18.0L26.0 14.0L52.0 10.0',
      area: 'M0.0 18.0L26.0 10.0L52.0 2.0L52.0 20L0.0 20Z',
    });
  });

  it('spaces each line over the whole width and keeps a flat line in the middle', () => {
    expect(sparkPaths([0, 0], [0, 0, 0], 52, 20)).toEqual({
      strategy: 'M0.0 10.0L52.0 10.0',
      benchmark: 'M0.0 10.0L26.0 10.0L52.0 10.0',
      area: 'M0.0 10.0L52.0 10.0L52.0 20L0.0 20Z',
    });
  });
});
