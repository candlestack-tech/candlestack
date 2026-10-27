import { TryBacktest } from './Buttons';
import { Pipeline } from './Pipeline';

// The headline, what the product does and the way in, over the pipeline every backtest takes.
// The headline is 32 px up to 640 px wide like in the design, so 46 px from 641 px, not from sm.
export function Hero() {
  return (
    <section className="grid grid-cols-[minmax(0,1fr)] gap-[26px] pt-10 pb-11">
      <div className="max-w-[760px]">
        <h1 className="max-w-[18ch] text-[32px] leading-[1.08] font-semibold tracking-[-0.035em] text-balance min-[641px]:text-[46px]">
          Backtest your models and compare every experiment
        </h1>
        <p className="mt-4 max-w-[54ch] text-[17px] text-muted-foreground">
          Run a trained model on real stock and crypto candles, try different configurations and see which one holds up.
          Change how prices are represented, which model predicts or how its predictions become trades. Every run is
          measured the same way.
        </p>
        <div className="mt-6 flex flex-wrap items-center gap-2.5">
          <TryBacktest />
        </div>
      </div>
      <Pipeline />
    </section>
  );
}
