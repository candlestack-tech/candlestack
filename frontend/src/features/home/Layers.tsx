import { Eyebrow } from '@/components/Eyebrow';

import { Section, SectionIntro } from './Section';

type Layer = { eyebrow: string; title: string; text: string; badges: string[] };

const LAYERS: Layer[] = [
  {
    eyebrow: 'Layer 1 · Representation',
    title: 'How prices become input',
    text: 'Turn raw candles into what your model reads: windows or bricks, with normalized indicators.',
    badges: ['Time windows', 'Renko'],
  },
  {
    eyebrow: 'Layer 2 · Model',
    title: 'What predicts',
    text: 'Bring your own trained model. We check that it fits the input before it runs.',
    badges: ['Keras', 'TensorFlow', 'PyTorch', 'ONNX', 'scikit-learn'],
  },
  {
    eyebrow: 'Layer 3 · Decision',
    title: 'How predictions become trades',
    text: 'Thresholds, direction, stop loss, smoothing, position size and costs.',
    badges: ['Threshold', 'Long only', 'Stop loss', 'Smoothing', 'Sizing'],
  },
];

function LayerCard({ layer }: { layer: Layer }) {
  return (
    <article className="flex flex-col gap-2 rounded-lg border bg-card p-[18px]">
      <Eyebrow>{layer.eyebrow}</Eyebrow>
      <h3 className="text-base font-bold text-balance">{layer.title}</h3>
      <p className="text-[13.5px] text-muted-foreground">{layer.text}</p>
      <ul className="flex flex-wrap items-center gap-1.5">
        {layer.badges.map((badge) => (
          <li
            key={badge}
            className="rounded-full border bg-card px-[9px] py-[3px] font-mono text-xs/[normal] font-medium"
          >
            {badge}
          </li>
        ))}
      </ul>
    </article>
  );
}

// The three layers a backtest runs through, side by side from 981 px.
export function Layers() {
  return (
    <Section id="layers">
      <SectionIntro
        eyebrow="How a run works"
        title="Three layers you can swap"
        text="Every backtest takes the same path from candles to trades. The three layers in the middle are what you change and compare."
      />
      <div className="mt-5 grid grid-cols-[minmax(0,1fr)] gap-3 min-[981px]:grid-cols-3">
        {LAYERS.map((layer) => (
          <LayerCard key={layer.title} layer={layer} />
        ))}
      </div>
    </Section>
  );
}
