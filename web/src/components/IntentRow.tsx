import type { AskIntentOut } from '../api/client'
import { filterPairs } from '../format'
import { Chip } from './Chip'

interface IntentRowProps {
  intent: AskIntentOut | null
}

/** "Understood as": one chip per intent code the parser produced. */
export function IntentRow({ intent }: IntentRowProps) {
  return (
    <section aria-labelledby="intent-heading" className="flex flex-col gap-2">
      <h2 id="intent-heading" className="text-sm font-bold text-muted-foreground">
        Understood as
      </h2>
      {intent === null ? (
        <p className="text-sm text-muted-foreground">
          The question could not be parsed into an intent.
        </p>
      ) : (
        <ul className="flex flex-wrap gap-2 [&>li]:min-w-0 [&>li]:max-w-full">
          {intent.decision_request && (
            <li>
              <Chip tone="destructive" mono>
                decision_request
              </Chip>
            </li>
          )}
          {intent.provider_filters && (
            <li>
              <Chip tone="accent" mono wrap>
                provider_filters: {filterPairs(intent.provider_filters).join(', ') || 'any'}
              </Chip>
            </li>
          )}
          {intent.price_query && (
            <li>
              <Chip tone="accent" mono wrap>
                price_query: {intent.price_query.item} · {intent.price_query.state}
                {intent.price_query.remoteness ? ` · ${intent.price_query.remoteness}` : ''}
              </Chip>
            </li>
          )}
          {intent.rule_question && (
            <li>
              <Chip tone="accent" mono wrap>
                rule_question: {intent.rule_question}
              </Chip>
            </li>
          )}
          {intent.parse_rejections.map((rejection) => (
            <li key={rejection}>
              <Chip tone="warning" mono wrap>
                rejected: {rejection}
              </Chip>
            </li>
          ))}
          {!intent.decision_request &&
            !intent.provider_filters &&
            !intent.price_query &&
            !intent.rule_question && (
              <li>
                <Chip>no actionable intent</Chip>
              </li>
            )}
        </ul>
      )}
    </section>
  )
}
