import type { MetaOut, StepOut } from '../api/client'
import { formatCost, formatMs } from '../format'
import { Disclosure } from './Disclosure'

interface HowBuiltProps {
  steps: StepOut[]
  meta: MetaOut | null
}

/** "How this answer was built": one row per pipeline step the server recorded. */
export function HowBuilt({ steps, meta }: HowBuiltProps) {
  const totalMs = steps.reduce((sum, step) => sum + step.ms, 0)
  const totalCost = steps.reduce((sum, step) => sum + (step.cost_usd ?? 0), 0)
  const traceUrl = meta?.tracing.configured ? meta.tracing.url : null
  return (
    <section aria-labelledby="how-built-heading">
      <h2 id="how-built-heading" className="sr-only">
        How this answer was built
      </h2>
      <Disclosure
        summary={`How this answer was built (${steps.length} ${steps.length === 1 ? 'step' : 'steps'}, ${formatMs(totalMs)})`}
      >
        <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[40rem] border-collapse text-sm">
              <caption className="sr-only">Pipeline steps in order</caption>
              <thead>
                <tr className="text-left text-xs text-muted-foreground">
                  <th scope="col" className="py-2 pr-3 font-bold">
                    Step
                  </th>
                  <th scope="col" className="py-2 pr-3 font-bold">
                    In → out
                  </th>
                  <th scope="col" className="py-2 pr-3 text-right font-bold">
                    Time
                  </th>
                  <th scope="col" className="py-2 pr-3 text-right font-bold">
                    Tokens in / out
                  </th>
                  <th scope="col" className="py-2 text-right font-bold">
                    Cost
                  </th>
                </tr>
              </thead>
              <tbody>
                {steps.map((step, index) => (
                  <tr key={`${step.kind}-${index}`} className="border-t border-border align-top">
                    <th
                      scope="row"
                      className="py-2 pr-3 text-left font-mono font-bold text-primary"
                    >
                      {step.kind}
                    </th>
                    <td className="py-2 pr-3">
                      <span className="text-muted-foreground">{step.input_summary}</span>
                      <span aria-hidden="true"> → </span>
                      <span className="sr-only"> to </span>
                      {step.output_summary}
                    </td>
                    <td className="py-2 pr-3 text-right font-mono whitespace-nowrap">
                      {formatMs(step.ms)}
                    </td>
                    <td className="py-2 pr-3 text-right font-mono whitespace-nowrap">
                      {step.input_tokens === null && step.output_tokens === null
                        ? '—'
                        : `${step.input_tokens ?? 0} / ${step.output_tokens ?? 0}`}
                    </td>
                    <td className="py-2 text-right font-mono whitespace-nowrap">
                      {step.cost_usd === null
                        ? step.input_tokens === null
                          ? '—'
                          : 'unpriced'
                        : formatCost(step.cost_usd)}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t-2 border-border text-xs text-muted-foreground">
                  <th scope="row" className="py-2 pr-3 text-left font-bold">
                    Total
                  </th>
                  <td />
                  <td className="py-2 pr-3 text-right font-mono">{formatMs(totalMs)}</td>
                  <td />
                  <td className="py-2 text-right font-mono">{formatCost(totalCost)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
          {traceUrl ? (
            <p className="text-sm">
              <a
                href={traceUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex min-h-[2.75rem] items-center text-accent underline"
              >
                Open the trace viewer
                <span className="sr-only"> (opens in a new tab)</span>
              </a>
            </p>
          ) : (
            <p className="text-xs text-muted-foreground">
              Tracing is not configured on this server.
            </p>
          )}
        </div>
      </Disclosure>
    </section>
  )
}
