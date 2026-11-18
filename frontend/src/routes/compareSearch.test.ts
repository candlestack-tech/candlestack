import { describe, expect, it } from 'vitest';

import { COMPARE_SLOTS, validateCompareSearch } from './compareSearch';

type RawSearch = Parameters<typeof validateCompareSearch>[0];
const validate = (search: Record<string, unknown>) => validateCompareSearch(search as RawSearch);

describe('validateCompareSearch', () => {
  it('keeps the backtest IDs in their order', () => {
    expect(validate({ ids: 'bt_t9n8v7d6a5,bt_7f3k2m9qx4' })).toEqual({ ids: 'bt_t9n8v7d6a5,bt_7f3k2m9qx4' });
    expect(validate({ ids: 'bt_12' })).toEqual({ ids: 'bt_12' });
  });

  it('takes the key repeated as well as one list', () => {
    expect(validate({ ids: ['bt_12', 'bt_11,bt_10'] })).toEqual({ ids: 'bt_12,bt_11,bt_10' });
  });

  it('drops what is no backtest ID, and repeats', () => {
    expect(validate({ ids: ' bt_12 ,mdl_7f3k2m9qx4,,bt_12,BT_11,bt_11,<script>' })).toEqual({ ids: 'bt_12,bt_11' });
    expect(validate({ ids: 'bt_' + 'x'.repeat(33) })).toEqual({ ids: undefined });
  });

  it('keeps the first four, the most the page compares', () => {
    expect(COMPARE_SLOTS).toBe(4);
    expect(validate({ ids: 'bt_1,bt_2,bt_3,bt_4,bt_5' })).toEqual({ ids: 'bt_1,bt_2,bt_3,bt_4' });
  });

  it('leaves the key undefined when nothing is left', () => {
    expect(validate({})).toEqual({ ids: undefined });
    expect(validate({ ids: '' })).toEqual({ ids: undefined });
    expect(validate({ ids: 42 })).toEqual({ ids: undefined });
    expect(validate({ ids: { id: 'bt_12' } })).toEqual({ ids: undefined });
  });
});
