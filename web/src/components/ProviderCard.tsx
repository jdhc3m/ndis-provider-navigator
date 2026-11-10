import type { AskProviderOut, ServiceMentionOut } from '../api/client'
import {
  DISTANCE_SOURCE_LABEL,
  REGISTRATION_LABEL,
  formatAbn,
  formatDate,
  formatKm,
  registrationBadge,
} from '../format'
import { Chip, type ChipTone } from './Chip'
import { Disclosure } from './Disclosure'

const BADGE_TONE: Record<ReturnType<typeof registrationBadge>, ChipTone> = {
  registered: 'success',
  revoked: 'destructive',
  'not-on-register': 'warning',
}

interface ProviderCardProps {
  provider: AskProviderOut
}

/** One register entry with its distance from the origin postcode (when the API measured
 * one, labelled exact or approximate), its verified service mentions and their evidence. */
export function ProviderCard({ provider }: ProviderCardProps) {
  const badge = registrationBadge(provider.registration_status)
  const office = provider.head_office
  const place = [office.suburb, office.state, office.postcode].filter(Boolean).join(' ')
  return (
    <article
      aria-labelledby={`provider-${provider.abn}`}
      className="flex flex-col gap-3 rounded-lg border border-border bg-card p-5 shadow-sm"
    >
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <h3
            id={`provider-${provider.abn}`}
            className="text-lg leading-tight font-bold text-primary"
          >
            {provider.business_name}
          </h3>
          <Chip tone={BADGE_TONE[badge]}>{REGISTRATION_LABEL[badge]}</Chip>
        </div>
        {provider.legal_name !== provider.business_name && (
          <p className="text-xs text-muted-foreground">{provider.legal_name}</p>
        )}
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
          <dt className="text-muted-foreground">ABN</dt>
          <dd className="font-mono">{formatAbn(provider.abn)}</dd>
          <dt className="text-muted-foreground">Head office</dt>
          <dd>{place || 'not recorded'}</dd>
          <dt className="text-muted-foreground">Distance</dt>
          {provider.distance ? (
            <dd className="flex flex-wrap items-baseline gap-x-2">
              <span className="font-bold text-foreground">{formatKm(provider.distance.km)}</span>
              <span className="text-xs text-muted-foreground">
                {DISTANCE_SOURCE_LABEL[provider.distance.source]}
              </span>
            </dd>
          ) : (
            <dd className="text-muted-foreground">not available</dd>
          )}
        </dl>
      </header>
      <section aria-label={`Services verified for ${provider.business_name}`}>
        {provider.services.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No services verified from this provider&apos;s website.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {provider.services.map((mention) => (
              <li key={`${mention.service}-${mention.url}`}>
                <ServiceTag mention={mention} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </article>
  )
}

function ServiceTag({ mention }: { mention: ServiceMentionOut }) {
  return (
    <div className="flex flex-col gap-1">
      <Disclosure
        size="sm"
        summary={
          <>
            <span className="text-foreground">{mention.service}</span>{' '}
            <span className="ml-1 font-normal text-muted-foreground">evidence</span>
          </>
        }
      >
        <figure className="rounded-md border-l-4 border-accent bg-background p-3 text-sm">
          <blockquote className="text-foreground">“{mention.quote}”</blockquote>
          <figcaption className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <a
              href={mention.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex min-h-[1.5rem] items-center break-all text-accent underline"
            >
              {mention.url}
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
            <span>
              fetched {mention.fetched_at ? formatDate(mention.fetched_at) : 'date unknown'}
            </span>
          </figcaption>
        </figure>
      </Disclosure>
    </div>
  )
}
