import { Link } from '@tanstack/react-router';

import { PageTitle } from '@/components/PageTitle';
import { usePageTitle } from '@/lib/usePageTitle';
import { Button } from '@/components/ui/button';

export function NotFoundPage() {
  usePageTitle('Page not found');
  return (
    <>
      <PageTitle title="Page not found" description="There is nothing at this address." />
      {/* Home, not the instrument list: that one is for signed-in accounts only (#340). */}
      <Button asChild variant="outline" className="self-start">
        <Link to="/">Back to home</Link>
      </Button>
    </>
  );
}
