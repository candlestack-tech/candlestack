import { queryOptions } from '@tanstack/react-query';

import { get, patch, put } from './client';
import type { components } from './schema';

type Schemas = components['schemas'];

export type Preferences = Schemas['PreferencesOut'];
export type BacktestDefaults = Schemas['BacktestDefaults'];
// What Save defaults sends: every field of BacktestDefaults, and no other.
export type BacktestDefaultsIn = Schemas['BacktestDefaultsIn'];
export type Appearance = Schemas['Appearance'];
type AppearanceUpdate = Schemas['AppearanceUpdate'];
export type Theme = Schemas['Theme'];
export type TimeZone = Schemas['TimeZone'];
export type DateFormat = Schemas['DateFormat'];
export type TimeFormat = Schemas['TimeFormat'];
export type Benchmark = Schemas['Benchmark'];
export type Direction = Schemas['Direction'];
// The timeframes a new backtest can start with: all of the chart's.
export type DefaultTimeframe = BacktestDefaults['timeframe'];

// GET /api/v1/me/preferences: the user's settings, the defaults for what they never saved. Under
// ['account', ...] with the user, so signing out forgets them. Fresh until the app changes them:
// every change goes through this app and updates the entry with the API's answer.
export function preferencesQueryOptions() {
  return queryOptions({
    queryKey: ['account', 'preferences'],
    queryFn: ({ signal }) => get<Preferences>('/v1/me/preferences', signal),
    staleTime: Infinity,
  });
}

// PUT /api/v1/me/preferences/backtest-defaults: the whole set at once (Save defaults); the API
// refuses a body that leaves a field out or adds one.
export const saveBacktestDefaults = (defaults: BacktestDefaultsIn) =>
  put<BacktestDefaults>('/v1/me/preferences/backtest-defaults', defaults);

// PATCH /api/v1/me/preferences/appearance: only what changed; the API answers with all of it.
export const updateAppearance = (update: AppearanceUpdate) =>
  patch<Appearance>('/v1/me/preferences/appearance', update);
