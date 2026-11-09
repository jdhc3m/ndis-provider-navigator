import type { ReactNode } from 'react'

export type ChipTone = 'neutral' | 'accent' | 'destructive' | 'success' | 'warning'

const TONE_CLASS: Record<ChipTone, string> = {
  neutral: 'bg-muted text-foreground border-border',
  accent: 'bg-accent-tint text-accent border-accent',
  destructive: 'bg-destructive-tint text-destructive-text border-destructive',
  success: 'bg-success-tint text-success border-success',
  warning: 'bg-warning-tint text-warning border-warning',
}

interface ChipProps {
  tone?: ChipTone
  children: ReactNode
  /** Monospace for codes such as item numbers and intent keys. */
  mono?: boolean
  title?: string
  /** Let long text wrap onto several lines instead of truncating it. */
  wrap?: boolean
}

/** A compact label. Never the only carrier of meaning: the text says it. */
export function Chip({ tone = 'neutral', children, mono = false, title, wrap = false }: ChipProps) {
  return (
    <span
      title={title}
      className={`inline-flex min-w-0 max-w-full items-center rounded-2xl border px-3 py-1 text-xs font-bold leading-tight ${wrap ? 'break-words' : 'whitespace-nowrap'} ${mono ? 'font-mono' : ''} ${TONE_CLASS[tone]}`}
    >
      <span className={wrap ? '' : 'truncate'}>{children}</span>
    </span>
  )
}
