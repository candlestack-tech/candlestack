import { useParams } from '@tanstack/react-router';

import { ApiError, errorMessage } from '@/api';
import { PageTitle } from '@/components/PageTitle';
import { BackLink, BacktestResult, useBacktestResult } from '@/features/backtests';
import { usePageTitle } from '@/lib/usePageTitle';

// Why there is nothing to show: no backtest of the user with the ID, a test backtest without a
// result document, or the request failed.
function NoBacktest({ error }: { error: Error }) {
  const missing = error instanceof ApiError && error.status === 404;
  const title = missing ? 'Not found' : 'Backtest';
  usePageTitle(title);
  return (
    <>
      <BackLink />
      <PageTitle
        variant="workspace"
        title={title}
        description={
          missing ? 'You have no backtest with this ID.' : errorMessage(error, 'Could not load the backtest.')
        }
      />
    </>
  );
}

// One backtest (#271, mockups/app pageRun): its result document, asked for again while it runs.
// /backtests/bt_7f3k2m9qx4
export function BacktestPage() {
  const { backtestId } = useParams({ from: '/_app/backtests/$backtestId' });
  const { data, error } = useBacktestResult(backtestId);
  usePageTitle(data ? (data.backtest.name ?? `Backtest #${data.backtest.number}`) : 'Backtest');
  if (data) return <BacktestResult doc={data} />;
  return error ? <NoBacktest error={error} /> : <BackLink />;
}
