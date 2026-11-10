import { exchangeName, type InstrumentDetail } from '@/api';
import { InstrumentIcon } from '@/components/InstrumentIcon';
import { formatDate, type DateStyle } from '@/lib/dates';

import { dayZone } from './time';

function Fact({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <div className={className}>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-mono text-sm">{value}</dd>
    </div>
  );
}

// Icon, name, symbol, exchange, source and since when there is data. The facts keep 40 px between
// them and wrap to a next line 12 px below when the screen is too narrow, as the mockup lays them
// out: on a phone two lines, the symbol first like on every other screen. A long id or name wraps
// instead of running off the screen. The first day is written as the user writes dates, as the
// market's day (dayZone), like the chart's daily candles: a crypto day is a UTC day.
export function InstrumentHeader({ instrument, dateStyle }: { instrument: InstrumentDetail; dateStyle: DateStyle }) {
  const since =
    instrument.available_from === null
      ? 'unknown'
      : formatDate(new Date(instrument.available_from * 1000), { ...dateStyle, timeZone: dayZone(instrument.market) });

  return (
    <div className="mb-6">
      <div className="flex items-center gap-3.5">
        <InstrumentIcon id={instrument.id} size={64} />
        <div className="min-w-0">
          <p className="font-mono text-sm break-all text-muted-foreground">{instrument.id}</p>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight break-words">{instrument.name}</h1>
        </div>
      </div>
      <dl className="mt-4 flex flex-wrap gap-x-10 gap-y-3">
        <Fact label="Symbol" value={instrument.symbol} />
        <Fact label="Exchange" value={exchangeName(instrument)} />
        <Fact label="Source" value={`${instrument.source} (${instrument.feed})`} />
        <Fact label="Data since" value={since} />
      </dl>
    </div>
  );
}
