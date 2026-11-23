import { useId, type KeyboardEvent, type Ref } from 'react'

interface ChatComposerProps {
  value: string
  onChange: (value: string) => void
  onSend: () => void
  /** A reply is in flight; the composer waits for it. */
  busy: boolean
  /** The thread is full; only "New chat" can continue. */
  disabled?: boolean
  examples: readonly string[]
  /** The example chips show only before the first message of a conversation. */
  showExamples: boolean
  ref?: Ref<HTMLTextAreaElement>
}

/** The chat composer: a labelled textarea that sends on Enter, adds a line on
 * Shift+Enter, and waits while a reply is pending. Example chips fill the box before
 * the first message. */
export function ChatComposer({
  value,
  onChange,
  onSend,
  busy,
  disabled = false,
  examples,
  showExamples,
  ref,
}: ChatComposerProps) {
  const inputId = useId()
  const hintId = useId()
  const trimmed = value.trim()
  const canSend = trimmed.length >= 3 && trimmed.length <= 500 && !busy && !disabled

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) {
      return
    }
    event.preventDefault()
    if (canSend) {
      onSend()
    }
  }

  return (
    <section aria-labelledby={`${inputId}-heading`} className="flex flex-col gap-3">
      <h2 id={`${inputId}-heading`} className="sr-only">
        Send a message
      </h2>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          if (canSend) {
            onSend()
          }
        }}
        className="flex flex-col gap-3"
      >
        <label htmlFor={inputId} className="text-lg font-bold text-primary">
          Your message
        </label>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
          <textarea
            id={inputId}
            name="message"
            rows={2}
            value={value}
            ref={ref}
            onChange={(event) => {
              onChange(event.target.value)
            }}
            onKeyDown={handleKeyDown}
            disabled={disabled}
            aria-describedby={hintId}
            maxLength={500}
            className="min-h-[2.75rem] flex-1 resize-y rounded-md border border-border bg-card px-4 py-3 text-base text-foreground placeholder:text-muted-foreground disabled:bg-muted disabled:text-muted-foreground"
            placeholder="Ask about providers, prices or the NDIS pricing rules — or continue the conversation"
          />
          <button
            type="submit"
            disabled={!canSend}
            className="tap-target transition-colors-fast cursor-pointer rounded-md bg-accent px-6 font-bold text-on-accent hover:bg-primary disabled:cursor-not-allowed disabled:bg-muted disabled:text-muted-foreground"
          >
            {busy ? 'Sending…' : 'Send'}
          </button>
        </div>
        <p id={hintId} className="text-xs text-muted-foreground">
          3 to 500 characters. Enter sends; Shift+Enter adds a line. Answers come from the public
          register, the support catalogue, the pricing rules document and quotes from
          providers&apos; own websites. The service never recommends a provider or makes funding
          decisions.
        </p>
      </form>
      {showExamples && (
        <div>
          <p className="mb-2 text-xs font-bold text-muted-foreground">Try an example</p>
          <ul className="flex flex-wrap gap-2">
            {examples.map((example) => (
              <li key={example}>
                <button
                  type="button"
                  onClick={() => {
                    onChange(example)
                  }}
                  className="tap-target transition-colors-fast cursor-pointer rounded-full border border-border bg-card px-4 py-2 text-left text-sm text-primary hover:border-accent hover:bg-accent-tint"
                >
                  {example}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
