import { useSearch } from '@tanstack/react-router';

import { errorMessage } from '@/api';
import { ErrorNote } from '@/components/ErrorNote';
import { PageTitle } from '@/components/PageTitle';
import { ExperimentsList, ViewSwitch, WhoseSwitch, useExperiments, useExperimentsView } from '@/features/backtests';
import { usePageTitle } from '@/lib/usePageTitle';

// The backtests people on the beta published (#368, public-experiments, pageExp): All · Mine and
// Cards · Table beside the title, then the search, the chips, the count and the list.
// /experiments?q=nvda&market=crypto&representation=renko&sort=sharpe
export function ExperimentsPage() {
  usePageTitle('Experiments');
  const search = useSearch({ from: '/_app/experiments' });
  const view = useExperimentsView();
  const result = useExperiments(search, view);
  return (
    <>
      <PageTitle
        variant="workspace"
        title="Experiments"
        description="Backtests people on the CandleStack beta published. Open one or add it to your Compare."
        action={
          <div className="flex flex-wrap items-center gap-2">
            <WhoseSwitch search={search} counts={result.first?.counts} />
            <ViewSwitch view={view} />
          </div>
        }
      />
      {result.error && !result.first && (
        <ErrorNote className="mb-3">{errorMessage(result.error, 'Could not load Experiments.')}</ErrorNote>
      )}
      <ExperimentsList search={search} view={view} result={result} />
    </>
  );
}
