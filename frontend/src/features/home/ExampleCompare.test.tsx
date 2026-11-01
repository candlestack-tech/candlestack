import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { ExampleCompare } from './ExampleCompare';

const html = () => renderToString(<ExampleCompare />);

describe('ExampleCompare', () => {
  it('says the figures are an example', () => {
    expect(html()).toContain('EXAMPLE');
    expect(html()).toContain('Sample figures, not market data');
  });

  it('marks one best and one worst value in each of the five rows', () => {
    expect(html().match(/text-up/g)).toHaveLength(5);
    expect(html().match(/text-down/g)).toHaveLength(5);
  });

  it('marks the highest return, and the lowest fees as the best', () => {
    const cell = (value: string) => html().split(value)[0]?.split('<td').at(-1) ?? '';
    expect(cell('+96.0 %')).toContain('text-up');
    expect(cell('−21.0 %')).toContain('text-down');
    expect(cell('3.1 %')).toContain('text-up');
    expect(cell('6.2 %')).toContain('text-down');
    expect(cell('4.4 %')).not.toMatch(/text-(up|down)/);
  });

  it('names no instrument: only models and rules', () => {
    expect(html()).not.toMatch(/AAPL|BTCUSDT|TSLA|NVDA/);
    expect(html()).toContain('LSTM · Threshold');
  });
});
