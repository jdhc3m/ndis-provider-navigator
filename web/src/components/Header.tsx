import type { MetaOut } from '../api/client'
import { formatDate } from '../format'

interface HeaderProps {
  meta: MetaOut | null
  /** The failure message when `/meta` could not be loaded; the page keeps working. */
  metaError: string | null
}

/** Title plus the facts the page labels itself with, all read from `GET /meta`. */
export function Header({ meta, metaError }: HeaderProps) {
  return (
    <header className="border-b border-border bg-card">
      <div className="mx-auto flex max-w-6xl flex-col gap-3 px-4 py-6 sm:px-6">
        <div>
          <p className="text-xs font-bold tracking-wide text-muted-foreground uppercase">
            Brisbane · grounded, refusable answers
          </p>
          <h1 className="text-2xl leading-tight font-bold text-primary">NDIS Provider Navigator</h1>
        </div>
        {meta ? (
          <dl className="grid grid-cols-1 gap-x-8 gap-y-1 text-xs text-muted-foreground sm:grid-cols-2 lg:grid-cols-4">
            <MetaItem
              label="Register snapshot"
              value={
                meta.register_snapshot_date
                  ? formatDate(meta.register_snapshot_date)
                  : 'date not available'
              }
            />
            <MetaItem label="Price limits" value={meta.catalogue_title} />
            <MetaItem label="Pricing rules" value={meta.pricing_document} />
            <MetaItem
              label="Model"
              value={
                meta.llm_configured && meta.llm_model ? meta.llm_model : 'model not configured'
              }
            />
          </dl>
        ) : (
          <p className="text-xs text-muted-foreground" role="status">
            {metaError
              ? `Service details unavailable: ${metaError}. Questions still work.`
              : 'Loading service details…'}
          </p>
        )}
      </div>
    </header>
  )
}

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 flex-col">
      <dt className="font-bold">{label}</dt>
      <dd className="text-foreground">{value}</dd>
    </div>
  )
}
