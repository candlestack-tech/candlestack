import { useQuery } from '@tanstack/react-query';
import { ChevronDown } from 'lucide-react';
import { useState } from 'react';

import { errorMessage, type Market } from '@/api';
import { DROPDOWN_CHEVRON, DROPDOWN_TRIGGER } from '@/components/buttonStyles';
import { Input } from '@/components/ui/input';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { useDebouncedValue } from '@/lib/useDebouncedValue';
import { cn } from '@/lib/utils';

import type { ChosenInstrument } from './form';
import { pickerQuery } from './query';

const NOTE = 'p-2 text-[12px] text-muted-foreground';

type OptionsProps = {
  query: string;
  market: Market;
  chosen: ChosenInstrument | undefined;
  onChoose: (instrument: ChosenInstrument) => void;
};

// An instrument of the list: its symbol in mono, then its name.
function Option({ item, chosen, onChoose }: { item: ChosenInstrument } & Omit<OptionsProps, 'query' | 'market'>) {
  const picked = item.id === chosen?.id;
  return (
    <li>
      <button
        type="button"
        aria-pressed={picked}
        onClick={() => onChoose(item)}
        className={cn(
          'flex h-8 w-full cursor-pointer items-center gap-2 rounded-sm px-2 text-left outline-none hover:bg-accent focus-visible:bg-accent',
          picked && 'text-primary',
        )}
      >
        <b className="min-w-[74px] font-mono text-[13px] font-semibold">{item.symbol}</b>
        <span className="min-w-0 truncate text-muted-foreground">{item.name}</span>
      </button>
    </li>
  );
}

// The instruments of the market that match the search, or why there are none.
function Options({ query, market, chosen, onChoose }: OptionsProps) {
  const { data, error } = useQuery(pickerQuery(query, market));
  if (error) return <p className={NOTE}>{errorMessage(error, 'Could not search.')}</p>;
  if (!data) return null;
  const items = data.items.filter((item) => item.market === market);
  if (items.length === 0) {
    const what = market === 'stock' ? 'Stocks' : 'Crypto pairs';
    return (
      <p className={NOTE}>{data.unavailable.includes(market) ? `${what} cannot be listed right now` : 'No match'}</p>
    );
  }
  return (
    <ul aria-label="Instruments" className="flex max-h-[280px] flex-col overflow-y-auto">
      {items.map((item) => (
        <Option key={item.id} item={item} chosen={chosen} onChoose={onChoose} />
      ))}
    </ul>
  );
}

type Props = {
  market: Market;
  chosen: ChosenInstrument | undefined;
  onChoose: (instrument: ChosenInstrument) => void;
};

// The button: the symbol and the name of the chosen one.
function Trigger({ chosen }: { chosen: ChosenInstrument | undefined }) {
  return (
    <PopoverTrigger
      aria-label={chosen ? `Instrument: ${chosen.symbol}` : 'Instrument'}
      className={cn(DROPDOWN_TRIGGER, 'max-w-full min-w-0 data-[state=open]:border-primary')}
    >
      {chosen ? (
        <>
          <b className="font-mono text-[13px] font-semibold text-foreground">{chosen.symbol}</b>
          <span className="min-w-0 truncate text-foreground">{chosen.name}</span>
        </>
      ) : (
        <span>Choose</span>
      )}
      <ChevronDown aria-hidden className={DROPDOWN_CHEVRON} />
    </PopoverTrigger>
  );
}

// One instrument of the model's market (.fp-t.b-it with .b-ip): the symbol and the name on the
// button, a search over the list in its panel. Picking one closes it.
export function InstrumentPicker({ market, chosen, onChoose }: Props) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const query = useDebouncedValue(text.trim(), 250);
  const close = () => {
    setOpen(false);
    setText('');
  };
  return (
    <Popover open={open} onOpenChange={(next) => (next ? setOpen(true) : close())}>
      <Trigger chosen={chosen} />
      <PopoverContent align="start" className="w-[min(340px,calc(100vw-48px))] p-2">
        <Input
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="Search"
          aria-label="Search instruments"
          maxLength={100}
          className="mb-1.5 h-8"
        />
        <Options
          query={query}
          market={market}
          chosen={chosen}
          onChoose={(instrument) => {
            onChoose(instrument);
            close();
          }}
        />
      </PopoverContent>
    </Popover>
  );
}
