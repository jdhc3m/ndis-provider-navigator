import type { PriceLimitOut } from '../api/client'
import { formatDate, formatMoney, remotenessLabel, unitLabel } from '../format'
import { Chip } from './Chip'

interface PriceLimitCardProps {
  price: PriceLimitOut
}

const TIERS = ['national', 'remote', 'very_remote'] as const

/** The catalogue price limit: copied from the support catalogue, never generated. */
export function PriceLimitCard({ price }: PriceLimitCardProps) {
  return (
    <article
      aria-labelledby="price-heading"
      className="flex flex-col gap-4 rounded-lg border border-border bg-card p-5 shadow-sm"
    >
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="price-heading" className="text-lg font-bold text-primary">
            Price limit
          </h2>
          <Chip tone="success">from support catalogue — not generated</Chip>
        </div>
        <p className="font-mono text-sm text-muted-foreground">{price.item_number}</p>
        <p className="text-base font-bold">{price.name}</p>
      </header>
      <div>
        <p className="text-3xl leading-none font-bold text-primary">
          {price.price_limit === null ? (
            <span className="text-xl">
              {price.quotable ? 'Quotable item' : 'No limit published'}
            </span>
          ) : (
            formatMoney(price.price_limit)
          )}
        </p>
        <p className="mt-1 text-sm text-muted-foreground">
          {unitLabel(price.unit)} · {price.state} · {remotenessLabel(price.remoteness)}
        </p>
      </div>
      <dl className="grid grid-cols-3 gap-2 text-sm">
        {TIERS.map((tier) => (
          <div key={tier} className="rounded-md bg-background p-2">
            <dt className="text-xs text-muted-foreground">{remotenessLabel(tier)}</dt>
            <dd className="font-bold">
              {price.limits[tier] === null ? 'none' : formatMoney(price.limits[tier])}
            </dd>
          </div>
        ))}
      </dl>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <dt>Registration group</dt>
        <dd>
          {price.registration_group_number} {price.registration_group_name}
        </dd>
        <dt>Valid</dt>
        <dd>
          from {formatDate(price.start_date)}
          {price.end_date ? ` to ${formatDate(price.end_date)}` : ''}
          {price.legacy ? ' (legacy item)' : ''}
        </dd>
        <dt>Source</dt>
        <dd>{price.source}</dd>
      </dl>
    </article>
  )
}
