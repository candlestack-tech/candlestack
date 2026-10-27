import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { MiddleCut, Sparkline } from './widgets';

const spans = (html: string) => [...html.matchAll(/<span[^>]*>([^<]*)<\/span>/g)].map((match) => match[1]);

describe('MiddleCut', () => {
  it('keeps the end of a long name whole and lets the start shrink', () => {
    const name = 'BLESK_ATR06_R8_OHLC_LAM08_EOD_SPLIT_ATR_NO-VOL_OPT1_8_M25x23.keras';
    const html = renderToString(<MiddleCut text={name} />);
    expect(spans(html)).toEqual(['BLESK_ATR06_R8_OHLC_LAM08_EOD_SPLIT_ATR_NO-VOL', '_OPT1_8_M25x23.keras']);
    expect(html).toContain(`title="${name}"`);
  });

  it('keeps the space before the end in the end, where a trimmed start would lose it', () => {
    expect(spans(renderToString(<MiddleCut text="my long model name for testing.keras" />))).toEqual([
      'my long model name',
      ' for testing.keras',
    ]);
  });

  it('leaves a short name in one piece', () => {
    expect(spans(renderToString(<MiddleCut text="blesk-novol-8.keras" />))).toEqual(['blesk-novol-8.keras']);
  });
});

describe('Sparkline', () => {
  const equity = { strategy: [1, 0.5, 0.01], benchmark: [1, 1.1, 1.3] };

  it('draws a loss in red and a gain in green', () => {
    expect(renderToString(<Sparkline equity={equity} total={-0.997} />)).toContain('stroke-down');
    expect(renderToString(<Sparkline equity={equity} total={0.223} />)).toContain('stroke-up');
  });
});
