import { useCallback, useEffect, useRef, useState } from 'react'

import type { AskOut, MetaOut, NavigatorApi } from '../api/client'
import { AnswerResult } from './AnswerResult'
import { ErrorState } from './ErrorState'
import { LoadingState } from './LoadingState'
import { QueryForm } from './QueryForm'

type AskState =
  | { status: 'idle' }
  | { status: 'loading'; question: string }
  | { status: 'answered'; answer: AskOut }
  | { status: 'error'; question: string; error: unknown }

interface SearchPageProps {
  client: NavigatorApi
  meta: MetaOut | null
  /** A question from the `?q=` link parameter: pre-filled and asked once on load. */
  initialQuestion?: string
}

/** The search tab: one question in, one answer out — the original page, unchanged. */
export function SearchPage({ client, meta, initialQuestion }: SearchPageProps) {
  const startQuestion = initialQuestion?.trim() ?? ''
  const [question, setQuestion] = useState(startQuestion)
  const [state, setState] = useState<AskState>(() =>
    startQuestion ? { status: 'loading', question: startQuestion } : { status: 'idle' },
  )
  const requestId = useRef(0)

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
    <div className="flex flex-col gap-8">
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
        <AnswerResult
          answer={state.answer}
          meta={meta}
          onPickExample={(example) => {
            setQuestion(example)
          }}
          onAsk={(text) => {
            setQuestion(text)
            ask(text)
          }}
        />
      )}
    </div>
  )
}
