import './widget.css';

import { connect, type Host, type Theme, type ToolResult } from './bridge';
import { cardOf, type Card } from './data';
import { h } from './dom';
import { filled, opens, render } from './view';

// The widget's start: it connects to the chat client, follows its theme, and draws each tool
// result it gets as a card, again whenever its width changes. Its height goes to the host, which
// sizes the frame to it.

const root = document.getElementById('root') as HTMLElement;

function applyTheme(theme: Theme | null) {
  const system = window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  document.documentElement.dataset.theme = theme ?? system;
}

// What the frame shows when a card cannot be drawn: the answer is in the chat anyway.
const failed = () => h('p', { class: 'note' }, 'This result could not be drawn here; the answer above has it.');

function show(host: Host) {
  let card: Card | null = null;
  let width = 0;
  // Each result gets a number: what is asked for an earlier one is dropped when it comes.
  let shown = 0;
  const draw = () => {
    width = root.clientWidth;
    if (!card) return root.replaceChildren();
    const drawn = card;
    const open = (url: string) => {
      if (opens(drawn, url)) host.openLink(url);
    };
    try {
      root.replaceChildren(render(drawn, { width: Math.max(width, 280), open }));
    } catch {
      root.replaceChildren(failed());
    }
  };
  const onResult = (result: ToolResult) => {
    const mine = ++shown;
    card = cardOf(result);
    draw();
    if (!card) return;
    filled(card, result, host)
      .then((more) => {
        if (more && mine === shown) {
          card = more;
          draw();
        }
      })
      // Without the curves the card shows the answer alone, which is still right.
      .catch(() => undefined);
  };
  new ResizeObserver(() => {
    if (root.clientWidth !== width) draw();
    host.resized(Math.ceil(document.documentElement.getBoundingClientRect().height));
  }).observe(root);
  host.onResult(onResult);
}

async function start() {
  applyTheme(null);
  const connected = await connect();
  if (!connected) return;
  applyTheme(connected.theme);
  connected.host.onTheme(applyTheme);
  show(connected.host);
}

void start();
