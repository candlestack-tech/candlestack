import { useSuspenseQuery } from '@tanstack/react-query';
import { useParams, useSearch } from '@tanstack/react-router';

import { instrumentQueryOptions, instrumentTimeframe } from '@/api';
import { InstrumentCandles, InstrumentHeader, TimeframeSwitch } from '@/features/instrument';
import { BackToList } from '@/features/instruments';
import { useDateStyle } from '@/features/preferences';
import { marketTimeZone } from '@/lib/dates';
import { usePageTitle } from '@/lib/usePageTitle';

// /instrument/crypto:BTCUSDT?tf=1h, in the app without the sidebar. The route loader has already
// fetched the instrument.
export function InstrumentPage() {
  const { id } = useParams({ from: '/_app/instrument/$id' });
  const search = useSearch({ from: '/_app/instrument/$id' });
  const { data: instrument } = useSuspenseQuery(instrumentQueryOptions(id));
  // Only a timeframe the instrument has; otherwise its first one.
  const tf = instrumentTimeframe(search.tf, instrument.timeframes);
  // The zone is the user's setting; until it has loaded, the market's own.
  const dateStyle = useDateStyle(marketTimeZone(instrument.market));
  usePageTitle(`${instrument.symbol} ${tf}`);

  return (
    <>
      <BackToList />
      <InstrumentHeader instrument={instrument} dateStyle={dateStyle} />
      <div className="mb-3">
        <TimeframeSwitch timeframes={instrument.timeframes} current={tf} />
      </div>
      <InstrumentCandles key={`${instrument.id}/${tf}`} instrument={instrument} timeframe={tf} dateStyle={dateStyle} />
    </>
  );
}
