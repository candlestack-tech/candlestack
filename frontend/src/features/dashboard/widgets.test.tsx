import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { Sparkline } from './widgets';

describe('Sparkline', () => {
  const equity = { strategy: [1, 0.5, 0.01], benchmark: [1, 1.1, 1.3] };

  it('draws a loss in red and a gain in green', () => {
    expect(renderToString(<Sparkline equity={equity} total={-0.997} />)).toContain('stroke-down');
    expect(renderToString(<Sparkline equity={equity} total={0.223} />)).toContain('stroke-up');
  });
});
