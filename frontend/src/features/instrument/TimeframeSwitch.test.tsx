import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

import { TimeframeSwitch } from './TimeframeSwitch';

// The links' props as TimeframeSwitch passes them; the router itself is not needed for that.
const links: { state?: unknown }[] = [];
vi.mock('@tanstack/react-router', () => ({
  Link: (props: { state?: unknown; children?: React.ReactNode }) => {
    links.push(props);
    return <span>{props.children}</span>;
  },
}));

describe('TimeframeSwitch', () => {
  it('keeps the history state', () => {
    links.length = 0;
    renderToStaticMarkup(<TimeframeSwitch timeframes={['1h', '1d']} current="1h" />);
    expect(links).toHaveLength(2);
    for (const { state } of links) {
      expect(typeof state).toBe('function');
      expect((state as (prev: object) => object)({ from: 'dashboard' })).toEqual({ from: 'dashboard' });
    }
  });
});
