import type { AskRuleOut, MetaOut } from '../api/client'
import { Chip } from './Chip'

interface RuleCardProps {
  rule: AskRuleOut
  /** Names the document the citations point into; `null` while `/meta` is unavailable. */
  meta: MetaOut | null
}

/** A pricing-rule answer with page-verified citations, or the refusal that replaced it. */
export function RuleCard({ rule, meta }: RuleCardProps) {
  return (
    <article
      aria-labelledby="rule-heading"
      className="flex flex-col gap-4 rounded-lg border border-border bg-card p-5 shadow-sm"
    >
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="rule-heading" className="text-lg font-bold text-primary">
          Pricing rule
        </h2>
        {rule.refused ? (
          <Chip tone="warning">refused</Chip>
        ) : (
          <Chip tone="success">every citation verified on its page</Chip>
        )}
      </header>
      <p className="text-sm text-muted-foreground">
        <span className="font-bold">Question: </span>
        {rule.question}
      </p>
      {rule.refused ? (
        <p className="rounded-md border-l-4 border-warning bg-warning-tint px-3 py-2 text-sm text-warning">
          <span className="font-bold">Not answered: </span>
          <span className="font-mono">{rule.reason ?? 'unknown reason'}</span>
          {rule.reason === 'no_chunks_above_threshold' &&
            ' — nothing in the pricing document was similar enough to the question.'}
        </p>
      ) : (
        <>
          <p className="text-base">{rule.answer}</p>
          <section aria-label="Citations" className="flex flex-col gap-2">
            <h3 className="text-xs font-bold text-muted-foreground">
              Citations{meta ? ` — ${meta.pricing_document}` : ''}
            </h3>
            <ol className="flex flex-col gap-2">
              {rule.citations.map((citation, index) => (
                <li
                  key={`${citation.page}-${index}`}
                  className="rounded-md border-l-4 border-accent bg-background p-3 text-sm"
                >
                  <p className="mb-1 font-mono text-xs text-muted-foreground">
                    page {citation.page}
                  </p>
                  <blockquote>“{citation.quote}”</blockquote>
                </li>
              ))}
            </ol>
          </section>
        </>
      )}
      <p className="text-xs text-muted-foreground">
        {rule.top_similarity === null
          ? 'Similarity: not measured.'
          : `Top chunk similarity ${rule.top_similarity.toFixed(2)}`}
        {rule.refused
          ? ''
          : ' · judged by the configured model, then every quote checked verbatim.'}
      </p>
    </article>
  )
}
