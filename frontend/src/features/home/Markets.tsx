import type { ReactNode } from 'react';

import { useMe } from '@/features/account';

import { BrowseInstruments } from './Buttons';
import { Section, SectionIntro } from './Section';

const TEXT = 'Every US exchange through Alpaca since 2016, Binance spot pairs since their listing.';

// Which markets the data covers, with the way to the instrument list in the app; `children` (an
// example of the list) fill the right column.
export function Markets({ children }: { children: ReactNode }) {
  const signedIn = Boolean(useMe());
  return (
    <Section id="markets" split>
      <div>
        <SectionIntro
          eyebrow="Instruments"
          title="US stocks and crypto pairs, candles from 1m to 1d"
          text={signedIn ? TEXT : `${TEXT} Sign in to browse them and open their candles.`}
        />
        <BrowseInstruments signedIn={signedIn} />
      </div>
      <div>{children}</div>
    </Section>
  );
}
