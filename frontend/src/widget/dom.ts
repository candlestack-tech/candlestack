// Elements without a framework: the widget is one small file a chat client loads each time it
// shows a result, so it builds its few elements itself.

type Child = Node | string | null | undefined | false;
type Attributes = Record<string, string | number | undefined>;

const SVG = 'http://www.w3.org/2000/svg';

function fill(element: Element, attributes: Attributes, children: Child[]) {
  for (const [name, value] of Object.entries(attributes)) {
    if (value !== undefined) element.setAttribute(name, String(value));
  }
  for (const child of children) {
    if (child !== null && child !== undefined && child !== false) element.append(child);
  }
  return element;
}

// An HTML element: h('div', { class: 'tile' }, 'Sharpe', value).
export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attributes: Attributes = {},
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  return fill(document.createElement(tag), attributes, children) as HTMLElementTagNameMap[K];
}

// An SVG element.
export function s(tag: string, attributes: Attributes = {}, ...children: Child[]): SVGElement {
  return fill(document.createElementNS(SVG, tag), attributes, children) as SVGElement;
}

// The CandleStack mark of the header: the logo's candles on the accent.
export function logo(): SVGElement {
  return s(
    'svg',
    { width: 22, height: 22, viewBox: '0 0 32 32', role: 'img', 'aria-label': 'CandleStack', class: 'logo' },
    s('rect', { class: 'logo-tile', width: 32, height: 32, rx: 7 }),
    s(
      'g',
      { class: 'logo-candles' },
      s('rect', { x: 10, y: 12, width: 2, height: 14 }),
      s('rect', { x: 8, y: 16, width: 6, height: 8 }),
      s('rect', { x: 20, y: 6, width: 2, height: 18 }),
      s('rect', { x: 18, y: 10, width: 6, height: 10 }),
    ),
  );
}

export type Tone = 'up' | 'down' | undefined;

// What a card needs of the place it is drawn in: its width in pixels and how a link is opened.
export type View = { width: number; open: (url: string) => void };

// The header of a card: the logo, its title and line under it, and a pill.
export function header(title: string, subtitle: string, pill?: { text: string; tone?: 'warn' | 'down' }): HTMLElement {
  return h(
    'header',
    { class: 'head' },
    logo(),
    h(
      'div',
      { class: 'head-text' },
      h('div', { class: 'title', title }, title),
      h('div', { class: 'sub', title: subtitle }, subtitle),
    ),
    pill && h('span', { class: `pill ${pill.tone ?? ''}` }, pill.text),
  );
}

export function tiles(items: { label: string; value: string; tone?: Tone }[]): HTMLElement {
  return h(
    'div',
    { class: 'tiles' },
    ...items.map(({ label, value, tone }) =>
      h(
        'div',
        { class: 'tile' },
        h('span', { class: 'tile-label' }, label),
        h('span', { class: `tile-value ${tone ?? ''}` }, value),
      ),
    ),
  );
}

// A link the host opens: a frame in a chat cannot navigate itself.
export function button(text: string, url: string, open: (url: string) => void, primary = false): HTMLAnchorElement {
  const link = h(
    'a',
    { class: primary ? 'button primary' : 'button', href: url, target: '_blank', rel: 'noopener noreferrer' },
    text,
  );
  link.addEventListener('click', (event) => {
    event.preventDefault();
    open(url);
  });
  return link;
}

export function footer(...children: Child[]): HTMLElement {
  return h('footer', { class: 'foot' }, ...children);
}
