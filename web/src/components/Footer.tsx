import type { MetaOut } from '../api/client'
import { formatDate } from '../format'

/** The disclaimer and the sources every answer is drawn from. */
export function Footer({ meta }: { meta: MetaOut | null }) {
  return (
    <footer className="border-t border-border bg-card">
      <div className="mx-auto flex max-w-6xl flex-col gap-3 px-4 py-6 text-xs text-muted-foreground sm:px-6">
        <p>
          <span className="font-bold text-foreground">Disclaimer. </span>
          This page lists facts from public sources and quotes from providers&apos; own websites. It
          does not recommend providers, assess suitability or make funding decisions, and it is not
          affiliated with the NDIA or the NDIS Quality and Safeguards Commission. Check with the
          provider and your plan before relying on anything shown here.
        </p>
        <p>
          <span className="font-bold text-foreground">Sources. </span>
          NDIS Quality and Safeguards Commission provider register
          {meta?.register_snapshot_date
            ? ` (snapshot ${formatDate(meta.register_snapshot_date)})`
            : ''}
          ; {meta?.catalogue_title ?? 'the NDIS support catalogue'};{' '}
          {meta?.pricing_document ?? 'the NDIS pricing arrangements document'}; provider websites,
          quoted verbatim with the fetch date.
        </p>
      </div>
    </footer>
  )
}
