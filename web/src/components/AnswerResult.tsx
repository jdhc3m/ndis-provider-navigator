import type { ReactNode } from 'react'

import type { AskOut, MetaOut } from '../api/client'
import { EXAMPLE_QUESTIONS } from '../examples'
import { HowBuilt } from './HowBuilt'
import { IntentRow } from './IntentRow'
import { PriceLimitCard } from './PriceLimitCard'
import { ProvidersColumn } from './ProvidersColumn'
import { RefusalBanner } from './RefusalBanner'
import { RuleCard } from './RuleCard'
import { ServicePriceCard } from './ServicePriceCard'

interface AnswerResultProps {
  answer: AskOut
  meta: MetaOut | null
  onPickExample: (question: string) => void
  /** Re-asks a new question (the locality candidate buttons use it). */
  onAsk: (question: string) => void
  /** The chips the zero-match state offers; the chat passes its own first-message
   * examples, the search page the search ones. */
  examples?: readonly string[]
  /** A line shown above the intent row — the chat's "understood as a follow-up about"
   * disclosure; the search page passes nothing. */
  understoodExtra?: ReactNode
}

/** The full display of one answer: the "Understood as" row, the provider and pricing
 * columns (or the refusal banner), and "How this answer was built". Shared by the
 * search page and by every assistant reply in the chat. */
export function AnswerResult({
  answer,
  meta,
  onPickExample,
  onAsk,
  examples = EXAMPLE_QUESTIONS,
  understoodExtra,
}: AnswerResultProps) {
  const servicePrices = answer.service_prices ?? null
  const hasRightColumn =
    answer.price !== null ||
    answer.price_error !== null ||
    servicePrices !== null ||
    answer.rule !== null
  const serviceMatchCount =
    servicePrices?.total ?? (answer.price_error === 'unknown_service' ? 0 : null)
  return (
    <div className="flex flex-col gap-6">
      {understoodExtra && <div className="flex flex-col gap-2">{understoodExtra}</div>}
      <IntentRow
        intent={answer.intent}
        locality={answer.locality ?? null}
        serviceMatchCount={serviceMatchCount}
        question={answer.question}
        onPickCandidate={onAsk}
      />
      {answer.refused ? (
        <RefusalBanner answer={answer} />
      ) : (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[3fr_2fr]">
          <ProvidersColumn answer={answer} onPickExample={onPickExample} examples={examples} />
          <aside aria-label="Pricing" className="flex flex-col gap-6">
            {answer.price && <PriceLimitCard price={answer.price} />}
            {servicePrices && <ServicePriceCard prices={servicePrices} />}
            {answer.price_error === 'unknown_service' && (
              <p
                role="status"
                className="rounded-lg border-l-4 border-warning bg-warning-tint p-4 text-sm text-warning"
              >
                <span className="font-bold">No catalogue match: </span>
                no support item in the loaded catalogue matches{' '}
                <span className="font-bold">
                  &ldquo;{answer.intent?.price_query?.service ?? 'that service'}&rdquo;
                </span>
                , so no price is shown. Check the spelling, or search the catalogue with an item
                number.
              </p>
            )}
            {answer.price_error && answer.price_error !== 'unknown_service' && (
              <p
                role="status"
                className="rounded-lg border-l-4 border-warning bg-warning-tint p-4 text-sm text-warning"
              >
                <span className="font-bold">Price limit not found: </span>
                <span className="font-mono">{answer.price_error}</span>
              </p>
            )}
            {answer.rule && <RuleCard rule={answer.rule} meta={meta} />}
            {!hasRightColumn && (
              <p className="rounded-lg border border-border bg-card p-5 text-sm text-muted-foreground">
                This question did not ask about a price limit or a pricing rule.
              </p>
            )}
          </aside>
        </div>
      )}
      <HowBuilt steps={answer.steps} meta={meta} />
    </div>
  )
}
