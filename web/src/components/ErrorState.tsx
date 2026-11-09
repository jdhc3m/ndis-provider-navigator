import { ApiError, NETWORK_ERROR_STATUS } from '../api/client'

interface ErrorStateProps {
  error: unknown
  onRetry: () => void
}

/** Any failure of `/ask`: shows FastAPI's `detail` when the server sent one. */
export function ErrorState({ error, onRetry }: ErrorStateProps) {
  const apiError = error instanceof ApiError ? error : null
  const headline =
    apiError === null
      ? 'Something went wrong'
      : apiError.status === NETWORK_ERROR_STATUS
        ? 'Could not reach the service'
        : `The service answered with status ${apiError.status}`
  const detail = apiError?.detail ?? (error instanceof Error ? error.message : 'no further detail')
  return (
    <div
      role="alert"
      className="flex flex-col gap-3 rounded-lg border-2 border-destructive bg-destructive-tint p-5"
    >
      <h2 className="text-xl font-bold text-destructive-text">{headline}</h2>
      <p className="text-base text-foreground">
        <span className="font-bold">Detail: </span>
        {detail}
      </p>
      <div>
        <button
          type="button"
          onClick={onRetry}
          className="tap-target transition-colors-fast cursor-pointer rounded-md border-2 border-primary bg-card px-6 font-bold text-primary hover:bg-muted"
        >
          Try again
        </button>
      </div>
    </div>
  )
}
