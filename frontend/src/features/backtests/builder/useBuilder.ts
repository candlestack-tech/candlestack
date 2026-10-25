import { useMutation, useQuery, useQueryClient, useSuspenseQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useState } from 'react';

import { backtestQueryOptions, modelsQueryOptions, preferencesQueryOptions, startBacktest, type Model } from '@/api';
import { useModels } from '@/features/models';
import { useDateStyle } from '@/features/preferences';
import { formatDate } from '@/lib/dates';

import {
  backtestOf,
  canRun,
  dayOf,
  decisionProblems,
  executionProblems,
  formOf,
  LAST_STEP,
  marketOf,
  periodProblem,
  secondsOf,
  type BuilderForm,
  type ChosenInstrument,
  type DateWriter,
} from './form';
import { problemOf, type BuilderProblem } from './problems';
import { pickerQuery } from './query';

// A day as the user writes dates (Settings, Appearance), as a UTC day.
function useDateWriter(): DateWriter {
  const style = useDateStyle();
  return (day) => formatDate(new Date(secondsOf(day) * 1000), { timeZone: 'UTC', dateFormat: style.dateFormat });
}

// The model chosen while it can run, else the first that can.
function chosenModel(models: readonly Model[] | undefined, id: string | undefined): Model | undefined {
  const runnable = models?.filter(canRun) ?? [];
  return runnable.find((model) => model.id === id) ?? runnable[0];
}

// The choices, starting from the account's Backtest defaults, and what the API said about them;
// any change clears that, as the user is fixing it. The model and the instrument fall back to the
// first that fits: the first model that can run, the most traded instrument of its market.
function useChoices(modelId: string | undefined) {
  const defaults = useSuspenseQuery(preferencesQueryOptions()).data.backtest_defaults;
  const [form, setForm] = useState<BuilderForm>(() => formOf(defaults, modelId));
  const [problem, setProblem] = useState<BuilderProblem | null>(null);
  const model = chosenModel(useModels().data?.models, form.modelId);
  const market = model && marketOf(model);
  const list = useQuery({ ...pickerQuery('', market ?? 'stock'), enabled: market !== undefined });
  const instrument: ChosenInstrument | undefined =
    form.instrument?.market === market ? form.instrument : list.data?.items.find((item) => item.market === market);
  const replace = (next: BuilderForm) => {
    setForm(next);
    setProblem(null);
  };
  const change = (part: Partial<BuilderForm>) => replace({ ...form, ...part });
  return { form, replace, change, problem, setProblem, model, market, instrument };
}

// Which step is open and how far the user got. A step opens once the user got there and every
// step before it is right; Next stays off while the open one is not.
function useSteps(ready: readonly boolean[]) {
  const [step, setStep] = useState(0);
  const [reach, setReach] = useState(0);
  const canOpen = (index: number) => index <= reach && ready.slice(0, index).every(Boolean);
  return {
    step,
    setStep,
    canOpen,
    open: (index: number) => canOpen(index) && setStep(index),
    next: () => {
      if (step >= LAST_STEP || !ready[step]) return;
      setStep(step + 1);
      setReach(Math.max(reach, step + 1));
    },
    back: () => setStep(Math.max(0, step - 1)),
  };
}

// Run: starts the backtest and opens its page; a refusal goes back to the step it is about.
function useRun(onProblem: (problem: BuilderProblem) => void, context: () => Parameters<typeof problemOf>[1]) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const refresh = (key: readonly unknown[]) => void queryClient.invalidateQueries({ queryKey: key });
  return useMutation({
    mutationFn: startBacktest,
    onSuccess: (document) => {
      for (const key of [['account', 'backtests'], ['account', 'dashboard'], modelsQueryOptions().queryKey])
        refresh(key);
      // The answer is the running backtest's document: its page shows it at once and polls it.
      queryClient.setQueryData(backtestQueryOptions(document.backtest.id).queryKey, document);
      void navigate({ to: '/backtests/$backtestId', params: { backtestId: document.backtest.id } });
    },
    onError: (error) => {
      const problem = problemOf(error, context());
      // The model may be gone or changed: the list shows it as it is now.
      if (problem.step === 0) refresh(modelsQueryOptions().queryKey);
      onProblem(problem);
    },
  });
}

// What stops each step: no model, a period that cannot run, a window or a cost that is not right.
function checksOf(
  { form, model, instrument }: Pick<ReturnType<typeof useChoices>, 'form' | 'model' | 'instrument'>,
  today: string,
  write: DateWriter,
) {
  const period = periodProblem(form, instrument, model, today, write);
  const decision = decisionProblems(form);
  const execution = executionProblems(form);
  const ready = [
    model !== undefined,
    instrument !== undefined && period === undefined,
    decision.fields.size === 0,
    execution.fields.size === 0,
    true,
  ];
  return { period, decision, execution, ready };
}

// The builder's state: the choices, the steps, what stops each step and Run.
export function useBuilder(modelId: string | undefined) {
  const choices = useChoices(modelId);
  const { form, model, instrument, problem } = choices;
  const write = useDateWriter();
  const [today] = useState(() => dayOf(Date.now() / 1000));
  const { period, decision, execution, ready } = checksOf(choices, today, write);
  const steps = useSteps(ready);
  const run = useRun(
    (found) => {
      choices.setProblem(found);
      steps.setStep(found.step);
    },
    () => ({ symbol: instrument?.symbol ?? 'The instrument', write }),
  );
  const start = () => {
    if (!model || !instrument || !ready.every(Boolean) || run.isPending) return;
    choices.setProblem(null);
    run.mutate(backtestOf(form, model, instrument, Date.now() / 1000));
  };
  // What a step shows in red: what the API said about it, else what stops it.
  const stepProblem = (index: number) =>
    problem?.step === index ? problem.message : [undefined, period, decision.message, execution.message][index];
  return {
    ...choices,
    ...steps,
    ready: ready[steps.step] ?? false,
    invalid: { decision: decision.fields, execution: execution.fields },
    stepProblem,
    today,
    write,
    start,
    running: run.isPending,
    choose: (id: string) => choices.change({ modelId: id }),
  };
}
