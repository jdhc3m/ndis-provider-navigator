/** A static, non-animated placeholder while `/ask` runs. */
export function LoadingState({ question }: { question: string }) {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-live="polite"
      className="flex flex-col gap-3 rounded-lg border border-border bg-card p-5"
    >
      <p className="text-base font-bold text-primary">Working on your question…</p>
      <p className="text-sm text-muted-foreground">
        Parsing the intent, searching the register, looking up the catalogue and checking the
        pricing rules. Rule questions can take several seconds.
      </p>
      <p className="text-sm text-muted-foreground">
        <span className="font-bold">Question: </span>
        {question}
      </p>
      <div aria-hidden="true" className="grid grid-cols-1 gap-3 lg:grid-cols-[3fr_2fr]">
        <div className="h-24 rounded-lg bg-muted" />
        <div className="h-24 rounded-lg bg-muted" />
      </div>
    </div>
  )
}
