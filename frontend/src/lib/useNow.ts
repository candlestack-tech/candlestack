import { useEffect, useState } from 'react';

// The time now in milliseconds, taken again every second while `ticking`: the counters of a
// running backtest go on between the page's requests.
export function useNow(ticking: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!ticking) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [ticking]);
  return now;
}

// Whole seconds from `iso` to `now`, never below zero (the browser's clock may be behind the
// server's); null without a time.
export function secondsSince(iso: string | null, now: number): number | null {
  return iso === null ? null : Math.max(0, Math.floor((now - Date.parse(iso)) / 1000));
}
