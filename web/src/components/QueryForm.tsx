import { useId, type SubmitEvent } from 'react'

import { EXAMPLE_QUESTIONS } from '../examples'

interface QueryFormProps {
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  busy: boolean
}

/** The labelled question box, its submit button and the four example chips. */
export function QueryForm({ value, onChange, onSubmit, busy }: QueryFormProps) {
  const inputId = useId()
  const hintId = useId()
  const trimmed = value.trim()
  const canSubmit = trimmed.length >= 3 && trimmed.length <= 500 && !busy

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault()
    if (canSubmit) {
      onSubmit()
    }
  }

  return (
    <section aria-labelledby={`${inputId}-heading`} className="flex flex-col gap-3">
      <h2 id={`${inputId}-heading`} className="sr-only">
        Ask a question
      </h2>
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <label htmlFor={inputId} className="text-lg font-bold text-primary">
          Ask about NDIS providers in Brisbane or NDIS pricing
        </label>
        <div className="flex flex-col gap-3 sm:flex-row">
          <textarea
            id={inputId}
            name="question"
            rows={2}
            value={value}
            onChange={(event) => {
              onChange(event.target.value)
            }}
            aria-describedby={hintId}
            maxLength={500}
            className="min-h-[2.75rem] flex-1 resize-y rounded-md border border-border bg-card px-4 py-3 text-base text-foreground placeholder:text-muted-foreground"
            placeholder="For example: approved providers in postcode 4101 that offer speech pathology"
          />
          <button
            type="submit"
            disabled={!canSubmit}
            className="tap-target transition-colors-fast cursor-pointer rounded-md bg-accent px-6 font-bold text-on-accent hover:bg-primary disabled:cursor-not-allowed disabled:bg-muted disabled:text-muted-foreground"
          >
            {busy ? 'Asking…' : 'Ask'}
          </button>
        </div>
        <p id={hintId} className="text-xs text-muted-foreground">
          3 to 500 characters. Answers come from the public register, the support catalogue, the
          pricing rules document and quotes from providers&apos; own websites. The service never
          recommends a provider or makes funding decisions.
        </p>
      </form>
      <div>
        <p className="mb-2 text-xs font-bold text-muted-foreground">Try an example</p>
        <ul className="flex flex-wrap gap-2">
          {EXAMPLE_QUESTIONS.map((example) => (
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
    </section>
  )
}
