import type { SearchSchemaInput } from '@tanstack/react-router';

// New backtest can start with a model chosen (Backtest with it on a model's page):
// /backtests/new?model=mdl_7f3k2m9qx4. Anything that is no model ID is dropped, and the builder
// then chooses the first model that can run.
export type BuilderSearch = { model: string | undefined };

const MODEL_ID = /^mdl_[a-z0-9]{1,32}$/;

// The key stays, undefined when dropped: the router lays the result over the raw URL values.
export function validateBuilderSearch(search: { model?: unknown } & SearchSchemaInput): BuilderSearch {
  return { model: typeof search.model === 'string' && MODEL_ID.test(search.model) ? search.model : undefined };
}
