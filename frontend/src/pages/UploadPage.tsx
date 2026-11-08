import { useParams } from '@tanstack/react-router';

import { UploadLink } from '@/features/models';
import { usePageTitle } from '@/lib/usePageTitle';

// /upload/<token>: the upload page of a one-time link an agent asked for (design/mockups/api-mcp,
// #upload/*), without the sidebar. Signed in only: signed out, the sign-in page returns here.
export function UploadPage() {
  usePageTitle('Upload a model');
  const { token } = useParams({ from: '/_app/upload/$token' });
  return <UploadLink key={token} token={token} />;
}
