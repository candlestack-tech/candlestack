import { Check } from 'lucide-react';

import { TryBacktest } from './Buttons';
import { ExampleCompare } from './ExampleCompare';
import { Section, SectionIntro } from './Section';

const POINTS = ['Equity and drawdown on one chart', 'Any model, rule and position size'];

// Comparing backtests: what it does, and the compare page in small with sample figures.
export function Compare() {
  return (
    <Section id="compare" split>
      <div>
        <SectionIntro
          eyebrow="Compare"
          title="See which setup holds up"
          text="Put up to 4 backtests side by side. Every run gets the same metrics, so the best and the worst value stand out."
        />
        <ul className="mt-4 flex flex-col gap-2 text-muted-foreground">
          {POINTS.map((point) => (
            <li key={point} className="flex items-center gap-2">
              <Check aria-hidden="true" className="size-3.5 flex-none text-primary" />
              {point}
            </li>
          ))}
        </ul>
        <div className="mt-5">
          <TryBacktest />
        </div>
      </div>
      <ExampleCompare />
    </Section>
  );
}
