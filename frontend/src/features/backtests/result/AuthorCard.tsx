import { Link } from '@tanstack/react-router';

import type { BacktestDocument } from '@/api';
import { Hint } from '@/components/Hint';
import { useViewerDateStyle } from '@/features/preferences';
import { formatDate, formatTime } from '@/lib/dates';

import { AuthorAvatar } from '../experiments/AuthorAvatar';
import { firstName } from '../experiments/format';

// Another account's backtest: who published it and when, on top of the right column, and the way
// to the rest of theirs in Experiments (public-experiments, authorCard).
export function AuthorCard({ backtest }: { backtest: BacktestDocument['backtest'] }) {
  const style = useViewerDateStyle();
  const { author, published_at: publishedAt } = backtest;
  const published = publishedAt === null ? null : new Date(publishedAt);
  return (
    <section aria-label="Author" className="flex min-w-0 flex-col gap-2.5 rounded-lg border bg-card px-4 py-3.5">
      <div className="flex items-center gap-3">
        <AuthorAvatar name={author.name} size="lg" />
        <div className="flex min-w-0 flex-col">
          <b className="text-sm font-semibold [overflow-wrap:anywhere]">{author.name}</b>
          {published && (
            <Hint>
              Published {formatDate(published, style)} at {formatTime(published, style)}
            </Hint>
          )}
        </div>
      </div>
      <Link
        to="/experiments"
        search={{ author: author.id }}
        className="border-t pt-2.5 text-[13px] font-medium text-primary underline-offset-2 outline-none hover:underline focus-visible:underline"
      >
        More by {firstName(author.name)} →
      </Link>
    </section>
  );
}
