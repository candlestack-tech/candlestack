import { useSearch } from '@tanstack/react-router';

import { errorMessage, type BacktestsPage as Page } from '@/api';
import { PageTitle } from '@/components/PageTitle';
import { BacktestsList, useBacktestsPage } from '@/features/backtests';
import { plural } from '@/lib/plural';
import { usePageTitle } from '@/lib/usePageTitle';

// The line under the title (mockups/app, btCount): how many backtests there are and how many of
// them are public ("13 backtests · 2 public", public-experiments), or how many of them match
// ("4 of 15"); why they could not load; an empty line while they load.
function description(data: Page | undefined, error: Error | null): string {
  if (data) {
    const { all, public: published } = data.counts;
    if (data.total !== all) return `${data.total} of ${all}`;
    return published > 0 ? `${plural(all, 'backtest')} · ${published} public` : plural(all, 'backtest');
  }
  // A no-break space while they load keeps the line, so the title row does not move.
  return error ? errorMessage(error, 'Could not load the backtests.') : '\u00a0';
}

// The user's backtests (#208, mockups/app ddRuns): searched, filtered, sorted and paged by the API.
// /backtests?q=aapl&show=starred&model=lstm.keras&sort=sharpe&order=asc&page=2
export function BacktestsPage() {
  usePageTitle('Backtests');
  const search = useSearch({ from: '/_app/backtests' });
  const result = useBacktestsPage(search);
  return (
    <>
      <PageTitle variant="workspace" title="Backtests" description={description(result.data, result.error)} />
      <BacktestsList search={search} data={result.data} loading={result.isPlaceholderData} />
    </>
  );
}
