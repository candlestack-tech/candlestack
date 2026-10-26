import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';

import { preferencesQueryOptions, type Appearance } from '@/api';
import { useMe } from '@/features/account';
import { NEW_YORK, dateStyleOf, viewerTimeZone, type DateStyle } from '@/lib/dates';

// The account's appearance settings while someone is signed in and the API has sent them;
// undefined otherwise.
export function useAccountAppearance(): Appearance | undefined {
  const signedIn = Boolean(useMe());
  return useQuery({
    ...preferencesQueryOptions(),
    enabled: signedIn,
    select: (preferences) => preferences.appearance,
  }).data;
}

// How dates and times are written for whoever looks: the account's choice, zone and all. A visitor
// has no setting: the API's formats (12 Oct 2024, 16:05) in `visitorZone`, New York time unless
// the page knows a better one (the chart: its market's).
export function useDateStyle(visitorZone = NEW_YORK): DateStyle {
  const appearance = useAccountAppearance();
  return useMemo(
    () => (appearance ? dateStyleOf(appearance) : { ...dateStyleOf(), timeZone: visitorZone }),
    [appearance, visitorZone],
  );
}

// How a time is written that says when something happened here (a backtest ran, a model was
// added): on the viewer's own clock, whatever the time zone setting, which is for market times
// (candles, trades). The account's date and time formats still apply.
export function useViewerDateStyle(): DateStyle {
  const style = useDateStyle();
  return useMemo(() => ({ ...style, timeZone: viewerTimeZone() }), [style]);
}
