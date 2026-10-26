import { useBlocker } from '@tanstack/react-router';

import { OutlineButton, PrimaryButton } from '@/components/DialogButtons';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

const BLOCK = () => true;

// While a field of the card holds a value that cannot be saved, leaving it (another section,
// another page) asks first: Keep editing stays, Discard leaves without the change. Closing the
// browser's tab asks the browser's own question.
export function LeaveGuard({ when }: { when: boolean }) {
  const blocker = useBlocker({ shouldBlockFn: BLOCK, disabled: !when, enableBeforeUnload: when, withResolver: true });
  return (
    <Dialog open={blocker.status === 'blocked'} onOpenChange={(open) => !open && blocker.reset?.()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Discard the change?</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <DialogDescription className="text-sm text-foreground">
            A field still has a value to fix, so the change is not saved.
          </DialogDescription>
        </DialogBody>
        <DialogFooter>
          <OutlineButton onClick={blocker.reset}>Keep editing</OutlineButton>
          <PrimaryButton onClick={blocker.proceed}>Discard</PrimaryButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
