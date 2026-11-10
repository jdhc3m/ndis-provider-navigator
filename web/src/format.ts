import type { AskIntentOut, DistanceOut } from './api/client'

/** `11111111111` → `11 111 111 111`, the way the ABR prints it. */
export function formatAbn(abn: string): string {
  const digits = abn.replace(/\D/g, '')
  if (digits.length !== 11) {
    return abn
  }
  return `${digits.slice(0, 2)} ${digits.slice(2, 5)} ${digits.slice(5, 8)} ${digits.slice(8)}`
}

const DATE_FORMAT = new Intl.DateTimeFormat('en-AU', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  timeZone: 'UTC',
})

/** ISO `YYYY-MM-DD` → `9 Nov 2026`; anything else is returned untouched. */
export function formatDate(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
  if (!match) {
    return iso
  }
  return DATE_FORMAT.format(
    new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]))),
  )
}

const KM_FORMAT = new Intl.NumberFormat('en-AU', {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
})

/** `3.2` → `3.2 km`; the API already rounds to one decimal. */
export function formatKm(km: number): string {
  return `${KM_FORMAT.format(km)} km`
}

/** The muted label beside a distance: how it was measured. */
export const DISTANCE_SOURCE_LABEL: Record<DistanceOut['source'], string> = {
  exact: 'from Maps listing',
  postcode_centroid: 'approx. — postcode centroid',
}

const MONEY_FORMAT = new Intl.NumberFormat('en-AU', {
  style: 'currency',
  currency: 'AUD',
  minimumFractionDigits: 2,
})

/** Exact decimal string from the API (`"193.99"`) → `$193.99`. */
export function formatMoney(amount: string): string {
  const value = Number(amount)
  return Number.isFinite(value) ? MONEY_FORMAT.format(value) : amount
}

export type RegistrationBadge = 'registered' | 'revoked' | 'not-on-register'

/** The register's `registration_status` values are `Approved` and `Revoked`. */
export function registrationBadge(status: string): RegistrationBadge {
  switch (status) {
    case 'Approved':
      return 'registered'
    case 'Revoked':
      return 'revoked'
    default:
      return 'not-on-register'
  }
}

export const REGISTRATION_LABEL: Record<RegistrationBadge, string> = {
  registered: 'Registered',
  revoked: 'Revoked',
  'not-on-register': 'Not on register',
}

const UNIT_LABEL: Record<string, string> = {
  H: 'per hour',
  E: 'per each',
  D: 'per day',
  WK: 'per week',
  MON: 'per month',
  YR: 'per year',
}

export function unitLabel(unit: string): string {
  return UNIT_LABEL[unit] ?? `per ${unit}`
}

const REMOTENESS_LABEL: Record<string, string> = {
  national: 'National',
  remote: 'Remote',
  very_remote: 'Very remote',
}

export function remotenessLabel(remoteness: string): string {
  return REMOTENESS_LABEL[remoteness] ?? remoteness
}

/** `state=QLD postcode=4101`-style pairs for the filters the intent carried. */
export function filterPairs(filters: NonNullable<AskIntentOut['provider_filters']>): string[] {
  const pairs: string[] = []
  if (filters.state) pairs.push(`state: ${filters.state}`)
  if (filters.postcode) pairs.push(`postcode: ${filters.postcode}`)
  if (filters.group) pairs.push(`group: ${filters.group}`)
  if (filters.status) pairs.push(`status: ${filters.status}`)
  if (filters.brisbane_area !== null) {
    pairs.push(`Brisbane area: ${filters.brisbane_area ? 'yes' : 'no'}`)
  }
  return pairs
}

export function formatMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${ms} ms`
}

export function formatCost(cost: number): string {
  return cost < 0.01 ? `$${cost.toFixed(6)}` : `$${cost.toFixed(4)}`
}
