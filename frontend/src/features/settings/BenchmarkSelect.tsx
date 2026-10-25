import type { Benchmark } from '@/api';
import { Select, SelectContent, SelectItem, SelectItemText, SelectTrigger } from '@/components/ui/select';

// What each backtest is measured against: its own instruments bought and kept, or an index fund.
// The short name is the tag before each, B&H for buy and hold.
const BENCHMARKS: Record<Benchmark, { tag: string; name: string; about: string }> = {
  bh: { tag: 'B&H', name: 'Buy and hold', about: 'The same instruments, bought and kept' },
  SPY: { tag: 'SPY', name: 'SPDR S&P 500 ETF', about: 'The 500 largest US companies' },
  QQQ: { tag: 'QQQ', name: 'Invesco QQQ', about: 'The Nasdaq 100' },
  DIA: { tag: 'DIA', name: 'SPDR Dow Jones ETF', about: 'The 30 Dow Jones companies' },
};

const ORDER: readonly Benchmark[] = ['bh', 'SPY', 'QQQ', 'DIA'];

// .st-bk: the short name in mono on the darker surface.
function Tag({ children }: { children: string }) {
  return (
    <span className="min-w-10 flex-none rounded-[5px] bg-surface-2 px-1.5 py-0.5 text-center font-mono text-[11.5px]/[normal] font-semibold text-foreground">
      {children}
    </span>
  );
}

type Props = {
  value: Benchmark;
  onChange: (value: Benchmark) => void;
  // The ones that can be chosen, every one when left out; the others are shown as coming soon.
  available?: readonly Benchmark[];
};

// A 300 px button with the tag and the name of the choice; its 340 px list adds what each one is.
export function BenchmarkSelect({ value, onChange, available = ORDER }: Props) {
  const chosen = BENCHMARKS[value];
  return (
    <Select value={value} onValueChange={(next) => onChange(next as Benchmark)}>
      <SelectTrigger className="w-[300px] max-w-full" aria-label={`Benchmark: ${chosen.name}`}>
        <Tag>{chosen.tag}</Tag>
        <span className="min-w-0 flex-1 truncate text-foreground">{chosen.name}</span>
      </SelectTrigger>
      <SelectContent className="w-[340px]">
        {ORDER.map((key) => {
          const { tag, name, about } = BENCHMARKS[key];
          const usable = available.includes(key);
          return (
            <SelectItem
              key={key}
              value={key}
              text={name}
              disabled={!usable}
              className="data-disabled:cursor-default data-disabled:opacity-50 data-disabled:hover:bg-transparent"
            >
              <Tag>{tag}</Tag>
              <span className="flex min-w-0 flex-col">
                <SelectItemText>{name}</SelectItemText>
                <span className="text-[11.5px] text-muted-foreground">{usable ? about : 'Coming soon'}</span>
              </span>
            </SelectItem>
          );
        })}
      </SelectContent>
    </Select>
  );
}
