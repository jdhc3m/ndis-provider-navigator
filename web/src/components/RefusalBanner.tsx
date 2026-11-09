import type { AskOut } from '../api/client'

interface RefusalBannerProps {
  answer: AskOut
}

/** Replaces both result columns when the service refuses to answer. */
export function RefusalBanner({ answer }: RefusalBannerProps) {
  return (
    <section
      role="status"
      aria-labelledby="refusal-heading"
      className="flex flex-col gap-2 rounded-lg border-2 border-destructive bg-destructive-tint p-5"
    >
      <h2 id="refusal-heading" className="text-xl font-bold text-destructive-text">
        Not answered
      </h2>
      <p className="font-mono text-sm text-destructive-text">
        reason: {answer.reason ?? 'unknown'}
      </p>
      <p className="text-base text-foreground">
        {answer.explanation ?? 'The service declined to answer this question.'}
      </p>
    </section>
  )
}
