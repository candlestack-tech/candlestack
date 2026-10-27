import type { BacktestStep } from '@/api';

// A step of a run as the API sends it: pending unless `fields` say more, the other details null.
export const step = (id: BacktestStep['id'], fields: Partial<BacktestStep> = {}): BacktestStep => ({
  id,
  state: 'pending',
  started_at: null,
  duration_s: null,
  loaded: null,
  total: null,
  cached: null,
  loaded_until: null,
  candles: null,
  from_cache: null,
  waiting_until: null,
  load_s: null,
  input_s: null,
  predict_s: null,
  ...fields,
});

// The steps of the finished example, 162 seconds in all.
export const FINISHED_STEPS: BacktestStep[] = [
  step('candles', {
    state: 'done',
    started_at: '2026-10-17T14:05:00Z',
    duration_s: 150.2,
    loaded: 1,
    total: 1,
    cached: 0,
    loaded_until: '2024-06-03',
    candles: 29,
    from_cache: false,
  }),
  step('queue', { state: 'done', started_at: '2026-10-17T14:07:30.200Z', duration_s: 0.4 }),
  step('model', {
    state: 'done',
    started_at: '2026-10-17T14:07:30.600Z',
    duration_s: 9.8,
    load_s: 1.4,
    input_s: 2.1,
    predict_s: 6.3,
  }),
  step('trades', { state: 'done', started_at: '2026-10-17T14:07:40.400Z', duration_s: 1.6 }),
];
