import { useCallback, useEffect, useRef, useState } from 'react'

import type { AskOut, MetaOut, NavigatorApi } from './api/client'
import { EXAMPLE_QUESTIONS } from './examples'
import { ErrorState } from './components/ErrorState'
import { Footer } from './components/Footer'
import { Header } from './components/Header'
import { HowBuilt } from './components/HowBuilt'
import { IntentRow } from './components/IntentRow'
import { LoadingState } from './components/LoadingState'
import { PriceLimitCard } from './components/PriceLimitCard'
import { ProvidersColumn } from './components/ProvidersColumn'
import { QueryForm } from './components/QueryForm'
import { RefusalBanner } from './components/RefusalBanner'
import { RuleCard } from './components/RuleCard'

type AskState =
  | { status: 'idle' }
  | { status: 'loading'; question: string }
  | { status: 'answered'; answer: AskOut }
  | { status: 'error'; question: string; error: unknown }

interface AppProps {
  client: NavigatorApi
  /** A question from the `?q=` link parameter: pre-filled and asked once on load. */
  initialQuestion?: string
}

export function App({ client, initialQuestion }: AppProps) {
  const startQuestion = initialQuestion?.trim() ?? ''
  const [meta, setMeta] = useState<MetaOut | null>(null)
  const [metaError, setMetaError] = useState<string | null>(null)
  const [question, setQuestion] = useState(startQuestion)
  const [state, setState] = useState<AskState>(() =>
    startQuestion ? { status: 'loading', question: startQuestion } : { status: 'idle' },
  )
  const requestId = useRef(0)

  useEffect(() => {
    let cancelled = false
    client.meta().then(
      (value) => {
        if (!cancelled) setMeta(value)
      },
      (error: unknown) => {
        if (!cancelled) {
          setMetaError(error instanceof Error ? error.message : 'unknown error')
        }
      },
    )
    return () => {
      cancelled = true
    }
  }, [client])

  /** Sends one question; only the latest request may update the result. */
  const run = useCallback(
    (text: string) => {
      const id = ++requestId.current
      return client.ask(text).then(
        (answer) => {
          if (requestId.current === id) setState({ status: 'answered', answer })
        },
        (error: unknown) => {
          if (requestId.current === id) setState({ status: 'error', question: text, error })
        },
      )
    },
    [client],
  )

  function ask(text: string) {
    setState({ status: 'loading', question: text })
    void run(text)
  }

  useEffect(() => {
    if (startQuestion) void run(startQuestion)
  }, [run, startQuestion])

  return (
    <div className="flex min-h-dvh flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-10 focus:rounded-md focus:bg-primary focus:px-4 focus:py-3 focus:text-on-primary"
      >
        Skip to main content
      </a>
      <Header meta={meta} metaError={metaError} />
      <main
        id="main"
        className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-8 px-4 py-8 sm:px-6"
      >
        <QueryForm
          value={question}
          onChange={setQuestion}
          onSubmit={() => {
            ask(question.trim())
          }}
          busy={state.status === 'loading'}
        />
        {state.status === 'loading' && <LoadingState question={state.question} />}
        {state.status === 'error' && (
          <ErrorState
            error={state.error}
            onRetry={() => {
              ask(state.question)
            }}
          />
        )}
        {state.status === 'answered' && (
          <Result
            answer={state.answer}
            meta={meta}
            onPickExample={(example) => {
              setQuestion(example)
            }}
          />
        )}
      </main>
      <Footer meta={meta} />
    </div>
  )
}

interface ResultProps {
  answer: AskOut
  meta: MetaOut | null
  onPickExample: (question: string) => void
}

function Result({ answer, meta, onPickExample }: ResultProps) {
  const hasRightColumn =
    answer.price !== null || answer.price_error !== null || answer.rule !== null
  return (
    <div className="flex flex-col gap-6" aria-live="polite">
      <IntentRow intent={answer.intent} />
      {answer.refused ? (
        <RefusalBanner answer={answer} />
      ) : (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[3fr_2fr]">
          <ProvidersColumn
            answer={answer}
            onPickExample={onPickExample}
            examples={EXAMPLE_QUESTIONS}
          />
          <aside aria-label="Pricing" className="flex flex-col gap-6">
            {answer.price && <PriceLimitCard price={answer.price} />}
            {answer.price_error && (
              <p
                role="status"
                className="rounded-lg border-l-4 border-warning bg-warning-tint p-4 text-sm text-warning"
              >
                <span className="font-bold">Price limit not found: </span>
                <span className="font-mono">{answer.price_error}</span>
              </p>
            )}
            {answer.rule && <RuleCard rule={answer.rule} meta={meta} />}
            {!hasRightColumn && (
              <p className="rounded-lg border border-border bg-card p-5 text-sm text-muted-foreground">
                This question did not ask about a price limit or a pricing rule.
              </p>
            )}
          </aside>
        </div>
      )}
      <HowBuilt steps={answer.steps} meta={meta} />
    </div>
  )
}
