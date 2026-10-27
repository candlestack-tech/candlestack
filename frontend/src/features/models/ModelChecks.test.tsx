// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import type { Manifest, Model } from '@/api';
import { manifest, model, rawCandlesSummary } from '@/test/api';

import { ModelChecks } from './ModelOverview';

afterEach(cleanup);

const READY = model({ state: 'ready', input_shape: [25, 5], manifest_summary: rawCandlesSummary() });
const CHECKED = '2026-10-17T11:00:00Z';
const WITH_EXAMPLE = manifest(25, {
  example: { input: [Array.from({ length: 25 }, () => [0, 0, 0, 0, 0])], output: [[0.1]] },
});

// The trial run and the example, the last two checks, as screen readers hear them.
function workerChecks(item: Model, itsManifest: Manifest | undefined = manifest()) {
  render(<ModelChecks model={item} manifest={itsManifest} />);
  return screen
    .getAllByRole('listitem')
    .slice(-2)
    .map((row) => row.textContent);
}

describe('the trial run and the example on the model page', () => {
  it('are not run yet before the check of the manifest', () => {
    expect(workerChecks(READY)).toEqual(['Not yet: Trial runNot run yet', 'Not checked: No example']);
  });

  it('wait to compare the example of a manifest that has one', () => {
    expect(workerChecks(READY, WITH_EXAMPLE)).toEqual([
      'Not yet: Trial runNot run yet',
      'Not yet: ExampleNot compared yet',
    ]);
  });

  it('pass once the model is verified', () => {
    expect(workerChecks(model({ ...READY, state: 'verified', checked_at: CHECKED }), WITH_EXAMPLE)).toEqual([
      'Passed: Trial run',
      'Passed: Example matches',
    ]);
  });

  it('pass the trial run of a manifest without an example', () => {
    expect(workerChecks(model({ ...READY, checked_at: CHECKED }))).toEqual([
      'Passed: Trial run',
      'Not checked: No example',
    ]);
  });

  it('say why the trial run failed', () => {
    const error = 'The model failed on its input (ValueError).';
    const failed = model({ ...READY, checked_at: CHECKED, check_failed: 'trial_run', check_error: error });
    expect(workerChecks(failed, WITH_EXAMPLE)).toEqual([
      `Failed: Trial run failed${error}`,
      'Not yet: ExampleNot compared',
    ]);
  });

  it('say plainly that the example did not match, after a trial run that passed', () => {
    const error = "For the manifest's example the model gives up to 0.5 away from the expected output.";
    const failed = model({ ...READY, checked_at: CHECKED, check_failed: 'example', check_error: error });
    expect(workerChecks(failed, WITH_EXAMPLE)).toEqual(['Passed: Trial run', `Failed: Example does not match${error}`]);
  });

  it('are all still to come without a manifest', () => {
    render(<ModelChecks model={model()} manifest={undefined} />);
    expect(
      screen
        .getAllByRole('listitem')
        .slice(-3)
        .map((row) => row.textContent),
    ).toEqual(['Not yet: Manifest', 'Not yet: Trial run', 'Not yet: Example']);
  });
});
