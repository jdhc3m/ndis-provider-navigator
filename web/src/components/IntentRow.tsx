import type { AskIntentOut, LocalityOut } from '../api/client'
import { candidateQuestion, filterPairs, localityChipText } from '../format'
import { Chip } from './Chip'

interface IntentRowProps {
  intent: AskIntentOut | null
  /** The suburb resolution the API produced, when the question named one. */
  locality: LocalityOut | null
  /** The question the answer answered: the candidate buttons ask it again, plus a postcode. */
  question: string
  /** Re-asks the page with the candidate's postcode (the chosen one). */
  onPickCandidate: (question: string) => void
}

/** "Understood as": one chip per intent code the parser produced, plus the locality
 * resolution chip and — when a suburb matched several postcodes — the candidate buttons
 * that ask again with the chosen one. */
export function IntentRow({ intent, locality, question, onPickCandidate }: IntentRowProps) {
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
          {locality && (
            <li>
              <Chip tone="accent" mono wrap>
                {localityChipText(locality)}
                {locality.outcome === 'resolved' &&
                  locality.state &&
                  locality.postcode &&
                  ' · the postcode covers other suburbs too'}
              </Chip>
            </li>
          )}
          {locality?.outcome === 'locality_ambiguous' && (
            <li>
              <ul aria-label="Candidate postcodes" className="flex flex-wrap gap-2">
                {locality.candidates.map((candidate) => (
                  <li key={`${candidate.state}-${candidate.postcode}`}>
                    <button
                      type="button"
                      onClick={() => {
                        onPickCandidate(candidateQuestion(question, candidate))
                      }}
                      className="tap-target transition-colors-fast cursor-pointer rounded-2xl border border-border bg-card px-3 py-1 text-xs font-bold text-primary hover:border-accent hover:bg-accent-tint"
                    >
                      Ask again with {candidate.state} {candidate.postcode}
                    </button>
                  </li>
                ))}
              </ul>
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
