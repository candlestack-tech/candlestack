import type { ReactNode } from 'react';

import type { Model } from '@/api';
import { InstrumentSymbol } from '@/components/InstrumentSymbol';

import type { BuilderForm, ChosenInstrument, DateWriter } from './form';
import { costsLine, decisionLine, fillLine, periodLine, preprocessingLine } from './format';
import { StepError } from './parts';

type Props = {
  form: BuilderForm;
  model: Model;
  instrument: ChosenInstrument;
  write: DateWriter;
  problem: string | undefined;
  onEdit: (step: number) => void;
};

type Line = { step: number; title: string; main: ReactNode; detail?: string | undefined };

// A step in a line (.b-rev): its name, what it chose over the details, and Edit.
function SummaryLine({ line, onEdit }: { line: Line; onEdit: (step: number) => void }) {
  return (
    <div className="grid grid-cols-[110px_minmax(0,1fr)_auto] items-baseline gap-3 border-t py-3 first:border-t-0 first:pt-0 max-[640px]:grid-cols-[minmax(0,1fr)_auto]">
      <dt className="text-[13px] text-muted-foreground max-[640px]:col-span-full">{line.title}</dt>
      <dd className="m-0 flex min-w-0 flex-col">
        <b className="text-sm font-medium [overflow-wrap:anywhere]">{line.main}</b>
        {line.detail && (
          <span className="font-mono text-xs text-muted-foreground [overflow-wrap:anywhere]">{line.detail}</span>
        )}
      </dd>
      <button
        type="button"
        aria-label={`Edit ${line.title.toLowerCase()}`}
        className="cursor-pointer text-[13px] text-primary outline-none hover:underline focus-visible:underline"
        onClick={() => onEdit(line.step)}
      >
        Edit
      </button>
    </div>
  );
}

// What to know before the run (.b-warn).
function Warnings({ form }: { form: BuilderForm }) {
  const warnings = form.honestFill ? [] : ["Filling at the signal's close is optimistic."];
  if (warnings.length === 0) return null;
  return (
    <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
      {warnings.map((warning) => (
        <li
          key={warning}
          className="rounded-lg bg-[color-mix(in_srgb,var(--warn)_11%,var(--background))] px-3 py-[9px] text-[13px]"
        >
          {warning}
        </li>
      ))}
    </ul>
  );
}

// Step 5 (stepRun): the four steps in a line each, with Edit (the data's instrument with its icon),
// then the warnings.
export function RunStep({ form, model, instrument, write, problem, onEdit }: Props) {
  const summary = model.manifest_summary;
  const lines: Line[] = [
    { step: 0, title: 'Model', main: model.name, detail: summary ? preprocessingLine(summary) : undefined },
    {
      step: 1,
      title: 'Data',
      main: <InstrumentSymbol id={instrument.id} symbol={instrument.symbol} name={instrument.name} size={20} />,
      detail: periodLine(form, write),
    },
    { step: 2, title: 'Decision', main: decisionLine(form) },
    { step: 3, title: 'Execution', main: fillLine(form.honestFill), detail: costsLine(form) },
  ];
  return (
    <>
      <dl className="m-0 flex flex-col">
        {lines.map((line) => (
          <SummaryLine key={line.title} line={line} onEdit={onEdit} />
        ))}
      </dl>
      <Warnings form={form} />
      {problem && <StepError>{problem}</StepError>}
    </>
  );
}
