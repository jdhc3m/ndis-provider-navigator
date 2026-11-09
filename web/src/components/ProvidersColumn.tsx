import type { AskOut } from '../api/client'
import { EmptyState } from './EmptyState'
import { ProviderCard } from './ProviderCard'

interface ProvidersColumnProps {
  answer: AskOut
  onPickExample: (question: string) => void
  examples: readonly string[]
}

/** Left column: the caveat line, then the provider cards or the empty state. */
export function ProvidersColumn({ answer, onPickExample, examples }: ProvidersColumnProps) {
  const filters = answer.intent?.provider_filters ?? null
  if (filters === null) {
    return (
      <section aria-labelledby="providers-heading" className="flex flex-col gap-3">
        <h2 id="providers-heading" className="text-lg font-bold text-primary">
          Providers
        </h2>
        <p className="rounded-lg border border-border bg-card p-5 text-sm text-muted-foreground">
          This question did not ask about providers, so the register was not searched.
        </p>
      </section>
    )
  }
  return (
    <section aria-labelledby="providers-heading" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="providers-heading" className="text-lg font-bold text-primary">
          Providers
        </h2>
        <p className="text-sm text-muted-foreground">
          showing {answer.providers.length} of {answer.provider_total} matched
        </p>
      </div>
      {answer.caveat ? (
        <p className="rounded-md border-l-4 border-warning bg-warning-tint px-3 py-2 text-sm text-warning">
          <span className="font-bold">Caveat: </span>
          {answer.caveat}
        </p>
      ) : (
        answer.providers.length > 0 && (
          <p className="rounded-md border-l-4 border-border bg-muted px-3 py-2 text-sm text-muted-foreground">
            No services are shown: none could be verified from these providers&apos; websites.
          </p>
        )
      )}
      {answer.provider_total === 0 ? (
        <EmptyState filters={filters} onPickExample={onPickExample} examples={examples} />
      ) : (
        <ul className="flex flex-col gap-3">
          {answer.providers.map((provider) => (
            <li key={provider.abn}>
              <ProviderCard provider={provider} />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
