import type { ServicePricesOut } from '../api/client'
import { formatMoney, unitLabel } from '../format'
import { Chip } from './Chip'

interface ServicePriceCardProps {
  prices: ServicePricesOut
}

/** The price list for a service phrase: every row copied from the support catalogue,
 * never generated. When the question stated no state the national limit applies and the
 * card says so. */
export function ServicePriceCard({ prices }: ServicePriceCardProps) {
  return (
    <article
      aria-labelledby="service-prices-heading"
      className="flex flex-col gap-4 rounded-lg border border-border bg-card p-5 shadow-sm"
    >
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="service-prices-heading" className="text-lg font-bold text-primary">
            Price limits
          </h2>
          <Chip tone="success">from support catalogue — not generated</Chip>
        </div>
        <p className="text-sm text-muted-foreground">
          Showing {prices.items.length} of {prices.total} support items matching{' '}
          <span className="font-bold text-foreground">&ldquo;{prices.phrase}&rdquo;</span>
          {prices.state === null && ' — no state stated, so the national limit applies.'}
        </p>
      </header>
      <ul className="flex flex-col gap-2">
        {prices.items.map((item) => (
          <li
            key={item.item_number}
            className="flex flex-col gap-1 rounded-md bg-background px-3 py-2"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-x-3">
              <p className="min-w-0 text-sm font-bold">{item.name}</p>
              <p className="text-base leading-none font-bold text-primary">
                {item.price_limit === null ? (
                  <span className="text-sm">
                    {item.quotable ? 'Quotable item' : 'No limit published'}
                  </span>
                ) : (
                  formatMoney(item.price_limit)
                )}
              </p>
            </div>
            <p className="text-xs text-muted-foreground">
              <span className="font-mono">{item.item_number}</span> · {unitLabel(item.unit)}
              {item.legacy ? ' · legacy item' : ''}
              {item.quotable ? ' · quotable' : ''}
            </p>
          </li>
        ))}
      </ul>
      <p className="text-xs text-muted-foreground">
        Source: <span>{prices.source}</span>
      </p>
    </article>
  )
}
