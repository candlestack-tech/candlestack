import { instrumentSearchQueryOptions, type Market } from '@/api';

// The instruments the picker lists at a time, the most traded first.
const PICKER_LIMIT = 20;

// What the instrument picker asks for: the instruments of the model's market that match the
// search. With an empty search its first one is the builder's instrument until one is chosen.
export const pickerQuery = (q: string, market: Market) =>
  instrumentSearchQueryOptions({ q, market, limit: PICKER_LIMIT, offset: 0 });
