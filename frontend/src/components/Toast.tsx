import { Check } from 'lucide-react';
import { useEffect, useState } from 'react';

// How long a toast stays (public-experiments, toast()).
const SHOWN_MS = 2800;

// A short note that something was done (public-experiments, .toast): ink on the page's colour at
// the bottom middle, with a check, rising 8 px as it comes; gone after 2.8 s. A new note takes the
// place of the one before. `show` gives it, `toast` is what to render.
export function useToast() {
  const [note, setNote] = useState<{ text: string; key: number } | null>(null);
  useEffect(() => {
    if (!note) return;
    const timer = setTimeout(() => {
      setNote(null);
    }, SHOWN_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [note]);
  const show = (text: string) => {
    setNote((last) => ({ text, key: (last?.key ?? 0) + 1 }));
  };
  const toast = note && (
    <output
      key={note.key}
      className="fixed bottom-7 left-1/2 z-[70] flex -translate-x-1/2 animate-in items-center gap-2.5 rounded-lg bg-foreground px-3.5 py-2.5 text-[13.5px] whitespace-nowrap text-background shadow-[0_10px_28px_var(--shadow)] duration-200 ease-out fade-in slide-in-from-bottom-2 motion-reduce:animate-none max-[640px]:w-[calc(100vw-32px)] max-[640px]:whitespace-normal"
    >
      <Check aria-hidden="true" className="size-4 flex-none" />
      {note.text}
    </output>
  );
  return { toast, show };
}
