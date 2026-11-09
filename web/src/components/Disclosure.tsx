import { useId, useState, type ReactNode } from 'react'

interface DisclosureProps {
  summary: ReactNode
  children: ReactNode
  defaultOpen?: boolean
  /** Visual size of the toggle; both keep a 44px target. */
  size?: 'sm' | 'md'
}

/**
 * A button-driven disclosure: Enter and Space toggle it (native button semantics),
 * `aria-expanded` announces the state and `aria-controls` ties the panel to it.
 */
export function Disclosure({
  summary,
  children,
  defaultOpen = false,
  size = 'md',
}: DisclosureProps) {
  const [open, setOpen] = useState(defaultOpen)
  const panelId = useId()
  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => {
          setOpen((value) => !value)
        }}
        className={`tap-target transition-colors-fast inline-flex cursor-pointer items-center gap-2 rounded-md border border-border bg-card px-3 font-bold text-accent hover:bg-muted ${size === 'sm' ? 'text-xs' : 'text-sm'}`}
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 20 20"
          className={`h-4 w-4 shrink-0 fill-none stroke-current stroke-2 ${open ? 'rotate-90' : ''}`}
          style={{ transition: 'transform var(--duration-fast) ease' }}
        >
          <path d="M7 4l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span>{summary}</span>
      </button>
      <div id={panelId} hidden={!open} className="mt-2">
        {open ? children : null}
      </div>
    </div>
  )
}
