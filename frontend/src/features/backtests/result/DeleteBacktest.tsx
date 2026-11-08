import { useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useState } from 'react';

import type { BacktestDocument } from '@/api';

import { DeleteButton, DeleteDialog } from '../DeleteDialog';
import { forgetBacktest } from '../useBacktests';

// Delete on the user's own backtest, whatever its status, an outline button with a bin that turns
// red on hover and asks first (DELETE /api/v1/backtests/{id}). Then the page goes to Backtests and
// forgets the document.
export function DeleteBacktest({ backtest }: { backtest: BacktestDocument['backtest'] }) {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  return (
    <>
      <DeleteButton onClick={() => setOpen(true)}>Delete</DeleteButton>
      <DeleteDialog
        backtests={[backtest]}
        open={open}
        onOpenChange={setOpen}
        onDeleted={() => {
          void navigate({ to: '/backtests' }).then(() => forgetBacktest(queryClient, backtest.id));
        }}
      />
    </>
  );
}
