import type { Backtest, BacktestDocument, ComparedBacktest, Manifest } from '@/api';
import { manifestRepresentation } from '@/features/models';

import { describedAs } from './format';

// How a backtest's document names it (backtest-names): the name, and "AAPL · Kagi · 1h" where
// nothing else describes it.

// What the model of a backtest's document reads, as its manifest names it: "Candles", "Renko".
export function documentRepresentation(doc: BacktestDocument): string {
  const { input } = doc.model.manifest as unknown as Partial<Manifest>;
  return input ? manifestRepresentation(input.representation.id) : '';
}

// "Kagi · 1h", what follows the instrument under a backtest's title.
export const documentReads = (doc: BacktestDocument) => describedAs([documentRepresentation(doc), doc.data.timeframe]);

// "AAPL · Kagi · 1h".
export const documentDescription = (doc: BacktestDocument) =>
  describedAs([doc.data.instrument.symbol, documentReads(doc)]);

// The same of a backtest in the comparison, from its setup ("stock:AAPL" is AAPL).
export function comparedDescription({ setup }: ComparedBacktest): string {
  const symbol = setup.instrument.slice(setup.instrument.indexOf(':') + 1);
  const reads = setup.reads === null ? '' : manifestRepresentation(setup.reads);
  return describedAs([symbol, reads, setup.timeframe]);
}

// Whether the user gave the backtest a name of their own.
export const hasOwnName = (backtest: Pick<Backtest, 'name' | 'generated_name'>) =>
  backtest.name !== backtest.generated_name;
