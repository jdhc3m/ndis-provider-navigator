import type { AskIntentOut } from '../api/client'
import { filterPairs } from '../format'
import { Chip } from './Chip'

interface EmptyStateProps {
  filters: NonNullable<AskIntentOut['provider_filters']>
  onPickExample: (question: string) => void
  examples: readonly string[]
}

/**
 * No provider matched. The API returns no alternatives, so none are invented: the
 * applied filters are echoed and the user is pointed at broader questions.
 */
export function EmptyState({ filters, onPickExample, examples }: EmptyStateProps) {
  const pairs = filterPairs(filters)
  return (
    <div
      role="status"
      className="flex flex-col gap-4 rounded-lg border border-dashed border-border bg-card p-5"
    >
      <div>
        <p className="text-xl font-bold text-primary">0 matched</p>
        <p className="text-sm text-muted-foreground">
          No register entry matches every filter below. Nearest alternatives are not available yet:
          the register is searched exactly, so broaden the question instead.
        </p>
      </div>
      <div>
        <p className="mb-2 text-xs font-bold text-muted-foreground">Filters applied</p>
        <ul className="flex flex-wrap gap-2">
          {pairs.length === 0 ? (
            <li>
              <Chip>no filters</Chip>
            </li>
          ) : (
            pairs.map((pair) => (
              <li key={pair}>
                <Chip mono>{pair}</Chip>
              </li>
            ))
          )}
        </ul>
      </div>
      <div>
        <p className="mb-2 text-xs font-bold text-muted-foreground">
          Broaden your search: drop the postcode, use the state or &quot;Brisbane area&quot;, or try
          one of these
        </p>
        <ul className="flex flex-wrap gap-2">
          {examples.map((example) => (
            <li key={example}>
              <button
                type="button"
                onClick={() => {
                  onPickExample(example)
                }}
                className="tap-target transition-colors-fast cursor-pointer rounded-full border border-border bg-card px-4 py-2 text-left text-sm text-primary hover:border-accent hover:bg-accent-tint"
              >
                {example}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
