import { Check } from 'lucide-react';

// What a section lets you do, one line each by a check (.chks).
export function Checks({ items }: { items: readonly string[] }) {
  return (
    <ul className="mt-4 flex flex-col gap-2 text-[13.5px] text-muted-foreground">
      {items.map((item) => (
        <li key={item} className="flex items-start gap-2">
          <Check aria-hidden="true" className="mt-1 size-3.5 flex-none text-primary" />
          {item}
        </li>
      ))}
    </ul>
  );
}

// The line under an example (.ex): the EXAMPLE badge and what the figures are, as the home page
// shows no market data (#340).
export function ExampleLine({ children }: { children: string }) {
  return (
    <div className="mt-2 flex items-center justify-between gap-3 text-[11.5px] text-muted-foreground">
      <span className="rounded-full border px-2 py-0.5 font-mono text-[10px]/4 font-medium tracking-[0.06em]">
        EXAMPLE
      </span>
      <span>{children}</span>
    </div>
  );
}
