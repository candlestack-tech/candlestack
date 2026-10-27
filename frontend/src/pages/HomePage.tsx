import { Compare, ExampleInstruments, Experiments, Hero, Layers, Markets } from '@/features/home';
import { usePageTitle } from '@/lib/usePageTitle';

// The public start page (design repository, mockups/home): what a backtest is made of, an example
// of the instruments, and what comes next. It asks the API for no market data (#340). The column
// is the Layout's, with the design's 14 px text.
export function HomePage() {
  usePageTitle('');
  return (
    <div className="text-sm/[1.5]">
      <Hero />
      <Layers />
      <Markets>
        <ExampleInstruments />
      </Markets>
      <Compare />
      <Experiments />
    </div>
  );
}
