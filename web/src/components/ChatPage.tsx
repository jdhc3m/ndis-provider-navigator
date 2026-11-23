import { useCallback, useEffect, useRef, useState } from 'react'

import {
  ApiError,
  type ChatOptionOut,
  type ChatOut,
  type ChatTurnOut,
  type MetaOut,
  type NavigatorApi,
  type PendingOut,
} from '../api/client'
import { CHAT_EXAMPLE_QUESTIONS } from '../examples'
import { AnswerResult } from './AnswerResult'
import { ChatComposer } from './ChatComposer'
import { ErrorState } from './ErrorState'

interface ChatPageProps {
  client: NavigatorApi
  meta: MetaOut | null
}

/** The browser keeps only the thread id; the conversation itself lives in the server's
 * graph state and is fetched again from `GET /chat/{thread_id}` after a reload. */
const THREAD_STORAGE_KEY = 'ndis-provider-navigator-thread'

function readStoredThreadId(): string | null {
  try {
    return window.localStorage.getItem(THREAD_STORAGE_KEY)
  } catch {
    return null
  }
}

function writeStoredThreadId(threadId: string): void {
  try {
    window.localStorage.setItem(THREAD_STORAGE_KEY, threadId)
  } catch {
    // Storage may be unavailable (private mode); the conversation still works this session.
  }
}

function clearStoredThreadId(): void {
  try {
    window.localStorage.removeItem(THREAD_STORAGE_KEY)
  } catch {
    // Nothing to clear then.
  }
}

function threadNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.reason === 'thread_not_found'
}

function threadFull(error: unknown): boolean {
  return error instanceof ApiError && error.reason === 'thread_full'
}

/** One stored turn from an answered reply: the answer is required, and the standalone
 * question always names the message it was understood as. */
function turnFromReply(message: string, reply: ChatOut): ChatTurnOut | null {
  if (reply.answer === null) {
    return null
  }
  return {
    message,
    standalone_question: reply.standalone_question ?? message,
    rewritten: reply.rewritten,
    rewrite_reason: reply.rewrite_reason,
    answer: reply.answer,
  }
}

/** Reduced-motion users get an instant jump instead of a smooth scroll. */
function prefersReducedMotion(): boolean {
  return (
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )
}

type RestoreState =
  { status: 'restoring' } | { status: 'ready' } | { status: 'error'; error: unknown }

/** The chat tab: a conversation view over `POST /chat`, restored from the server after
 * a reload, with the example chips, the thread notices and the composer. */
export function ChatPage({ client, meta }: ChatPageProps) {
  const [initialThreadId] = useState(readStoredThreadId)
  const [restore, setRestore] = useState<RestoreState>(() =>
    initialThreadId ? { status: 'restoring' } : { status: 'ready' },
  )
  const [threadId, setThreadId] = useState<string | null>(initialThreadId)
  const [turns, setTurns] = useState<ChatTurnOut[]>([])
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState<string | null>(null)
  // The question a paused turn waits on, and the message that asked it (the server
  // stores neither in the turns until the turn completes; after a reload only the
  // question itself comes back, so the asked message is gone by design).
  const [pendingQuestion, setPendingQuestion] = useState<PendingOut | null>(null)
  const [askedMessage, setAskedMessage] = useState<string | null>(null)
  // The option id being submitted, which disables the buttons until the turn lands.
  const [resuming, setResuming] = useState<string | null>(null)
  const [resumeFailure, setResumeFailure] = useState<{
    option: ChatOptionOut
    error: unknown
  } | null>(null)
  const [failed, setFailed] = useState<{ message: string; error: unknown } | null>(null)
  const [notKept, setNotKept] = useState(false)
  const [threadFullError, setThreadFullError] = useState<ApiError | null>(null)
  const [restoreAttempt, setRestoreAttempt] = useState(0)
  const composerRef = useRef<HTMLTextAreaElement | null>(null)
  const logEndRef = useRef<HTMLDivElement | null>(null)
  // Bumped whenever the conversation is abandoned ("New chat", a lost thread): any
  // reply or restore that was still in flight belongs to the old conversation and must
  // not touch its state or the stored thread id.
  const generationRef = useRef(0)

  /** A conversation with nothing behind it: empty server thread, cleared storage, and —
   * when the old thread could not be restored — the notice that explains why. */
  const startFresh = useCallback((notKeptNotice: boolean) => {
    generationRef.current += 1
    setThreadId(null)
    setTurns([])
    setPending(null)
    setPendingQuestion(null)
    setAskedMessage(null)
    setResuming(null)
    setResumeFailure(null)
    setFailed(null)
    setThreadFullError(null)
    setNotKept(notKeptNotice)
    setDraft('')
    clearStoredThreadId()
  }, [])

  useEffect(() => {
    if (initialThreadId === null) {
      return
    }
    let cancelled = false
    const generation = generationRef.current
    client.getThread(initialThreadId).then(
      (thread) => {
        if (cancelled) {
          return
        }
        setRestore({ status: 'ready' })
        if (generationRef.current !== generation) {
          return
        }
        setTurns(thread.turns)
        setPendingQuestion(thread.pending ?? null)
        setAskedMessage(null)
      },
      (error: unknown) => {
        if (cancelled) {
          return
        }
        if (threadNotFound(error)) {
          setRestore({ status: 'ready' })
          startFresh(true)
        } else if (generationRef.current !== generation) {
          // The restore was abandoned mid-flight; there is nothing left to surface.
          setRestore({ status: 'ready' })
        } else {
          setRestore({ status: 'error', error })
        }
      },
    )
    return () => {
      cancelled = true
    }
    // The restore runs on mount and on an explicit retry (the attempt counter), from the
    // thread id the page arrived with; later thread changes go through `startFresh` and
    // `send`, never through this effect.
  }, [client, initialThreadId, restoreAttempt, startFresh])

  const send = useCallback(
    (text: string) => {
      const message = text.trim()
      if (
        pending !== null ||
        threadFullError !== null ||
        message.length < 3 ||
        message.length > 500
      ) {
        return
      }
      const generation = generationRef.current
      setDraft('')
      setPending(message)
      setFailed(null)
      setNotKept(false)
      // A new message instead of an answer: the pending question is dropped and the
      // message is handled as a normal turn from the start.
      setPendingQuestion(null)
      setAskedMessage(null)
      setResumeFailure(null)
      client.chat(message, threadId).then(
        (reply) => {
          if (generationRef.current !== generation) {
            return
          }
          setThreadId(reply.thread_id)
          writeStoredThreadId(reply.thread_id)
          if (reply.status === 'needs_input' && reply.pending !== null) {
            // The turn stopped to ask which place the user meant: the question shows
            // under the message, and nothing is stored until it is answered.
            setPendingQuestion(reply.pending)
            setAskedMessage(message)
          } else {
            const turn = turnFromReply(message, reply)
            if (turn !== null) {
              setTurns((previous) => [...previous, turn])
            }
          }
          setPending(null)
          composerRef.current?.focus()
        },
        (error: unknown) => {
          if (generationRef.current !== generation) {
            return
          }
          setPending(null)
          if (threadNotFound(error)) {
            // The server lost the conversation between turns: show the notice, clear the
            // thread, and keep the message so the user can send it into a fresh one.
            startFresh(true)
            setDraft(message)
          } else if (threadFull(error)) {
            setThreadFullError(error instanceof ApiError ? error : null)
            setDraft(message)
          } else {
            setFailed({ message, error })
            setDraft(message)
          }
          composerRef.current?.focus()
        },
      )
    },
    [client, pending, startFresh, threadFullError, threadId],
  )

  const chooseOption = useCallback(
    (option: ChatOptionOut) => {
      if (threadId === null || resuming !== null || pendingQuestion === null) {
        return
      }
      const generation = generationRef.current
      setResuming(option.id)
      setResumeFailure(null)
      client.resumeChat(threadId, option.id).then(
        (reply) => {
          if (generationRef.current !== generation) {
            return
          }
          setResuming(null)
          setPendingQuestion(null)
          setAskedMessage(null)
          if (reply.status === 'needs_input' && reply.pending !== null) {
            // The answer named none of the options: the same question, asked again with
            // the reason, and the buttons still there.
            setPendingQuestion(reply.pending)
          } else {
            const turn = turnFromReply(option.label, reply)
            if (turn !== null) {
              // The chosen option reads as the user's message; the same turn continues
              // below it with the answer.
              setTurns((previous) => [...previous, turn])
            }
          }
          composerRef.current?.focus()
        },
        (error: unknown) => {
          if (generationRef.current !== generation) {
            return
          }
          setResuming(null)
          setResumeFailure({ option, error })
        },
      )
    },
    [client, pendingQuestion, resuming, threadId],
  )

  useEffect(() => {
    const hasContent =
      turns.length > 0 || pending !== null || failed !== null || pendingQuestion !== null
    if (!hasContent) {
      return
    }
    const end = logEndRef.current
    if (typeof end?.scrollIntoView !== 'function') {
      return
    }
    end.scrollIntoView({
      behavior: prefersReducedMotion() ? 'auto' : 'smooth',
      block: 'end',
    })
  }, [turns.length, pending, failed, pendingQuestion, askedMessage])

  const restoring = restore.status === 'restoring'
  const emptyConversation =
    restore.status === 'ready' &&
    turns.length === 0 &&
    pending === null &&
    failed === null &&
    pendingQuestion === null

  return (
    <div className="flex flex-col gap-6">
      {notKept && (
        <div
          role="status"
          aria-label="Conversation not kept"
          className="rounded-lg border-l-4 border-warning bg-warning-tint p-4 text-sm text-warning"
        >
          <span className="font-bold">Conversation not kept: </span>that conversation is no longer
          on the server — it may have been lost in a restart. Your next message starts a new one.
        </div>
      )}
      {threadFullError && (
        <div
          role="alert"
          aria-label="Conversation full"
          className="flex flex-col gap-3 rounded-lg border-2 border-destructive bg-destructive-tint p-4"
        >
          <p className="text-sm text-foreground">
            <span className="font-bold text-destructive-text">Conversation full: </span>
            {threadFullError.detail ?? 'the thread holds its maximum number of turns'}
          </p>
          <div>
            <button
              type="button"
              onClick={() => {
                startFresh(false)
                composerRef.current?.focus()
              }}
              className="tap-target transition-colors-fast cursor-pointer rounded-md bg-accent px-4 font-bold text-on-accent hover:bg-primary"
            >
              New chat
            </button>
          </div>
        </div>
      )}
      {restore.status === 'error' && (
        <ErrorState
          error={restore.error}
          onRetry={() => {
            setRestoreAttempt((attempt) => attempt + 1)
          }}
        />
      )}
      {restoring && (
        <div
          role="status"
          aria-busy="true"
          className="rounded-lg border border-border bg-card p-5 text-sm text-muted-foreground"
        >
          Restoring your conversation…
        </div>
      )}
      {emptyConversation && (
        <p className="text-sm text-muted-foreground">
          Ask a first question, or start from an example under the box. Follow-up questions keep the
          conversation&apos;s context — the service carries it over for you.
        </p>
      )}
      <div role="log" aria-label="Conversation" className="flex flex-col gap-8">
        {turns.length > 0 && (
          <ol className="flex flex-col gap-8">
            {turns.map((turn, index) => (
              <li key={`${turn.message}-${index}`} className="flex flex-col gap-4">
                <UserMessage message={turn.message} />
                <div className="flex flex-col gap-4">
                  <AnswerResult
                    answer={turn.answer}
                    meta={meta}
                    onPickExample={setDraft}
                    onAsk={send}
                    examples={CHAT_EXAMPLE_QUESTIONS}
                    understoodExtra={
                      turn.rewritten ? (
                        <p className="text-sm text-muted-foreground">
                          Follow-up understood as{' '}
                          <span className="font-bold text-foreground">
                            &ldquo;{turn.standalone_question}&rdquo;
                          </span>{' '}
                          — the conversation context was carried over.
                        </p>
                      ) : turn.rewrite_reason ? (
                        <p className="text-sm text-warning">
                          The follow-up could not be rewritten ({turn.rewrite_reason}), so it was
                          answered exactly as written.
                        </p>
                      ) : null
                    }
                  />
                </div>
              </li>
            ))}
          </ol>
        )}
        {pending !== null && <PendingTurn message={pending} />}
        {askedMessage !== null && pendingQuestion !== null && (
          <UserMessage message={askedMessage} />
        )}
        {pendingQuestion !== null && (
          <PendingQuestion
            pending={pendingQuestion}
            busy={resuming !== null || pending !== null}
            onChoose={chooseOption}
          />
        )}
        {resumeFailure && (
          <div className="flex flex-col gap-4">
            <UserMessage message={resumeFailure.option.label} />
            <ErrorState
              error={resumeFailure.error}
              onRetry={() => {
                const option = resumeFailure.option
                setResumeFailure(null)
                chooseOption(option)
              }}
            />
          </div>
        )}
        {failed && (
          <div className="flex flex-col gap-4">
            <UserMessage message={failed.message} />
            <ErrorState
              error={failed.error}
              onRetry={() => {
                const message = failed.message
                setFailed(null)
                send(message)
              }}
            />
          </div>
        )}
        <div ref={logEndRef} aria-hidden="true" />
      </div>
      <ChatComposer
        value={draft}
        onChange={setDraft}
        onSend={() => {
          send(draft)
        }}
        busy={pending !== null}
        disabled={threadFullError !== null}
        examples={CHAT_EXAMPLE_QUESTIONS}
        showExamples={emptyConversation}
        ref={composerRef}
      />
      {(threadId !== null || turns.length > 0) && !threadFullError && (
        <div className="flex justify-end">
          <button
            type="button"
            onClick={() => {
              startFresh(false)
              composerRef.current?.focus()
            }}
            className="tap-target transition-colors-fast cursor-pointer rounded-md border-2 border-primary bg-card px-4 font-bold text-primary hover:bg-muted"
          >
            New chat
          </button>
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        Messages are kept on the server for this conversation only and are lost when the server
        restarts. Nothing is stored in this browser except the conversation&apos;s id.
      </p>
    </div>
  )
}

function UserMessage({ message }: { message: string }) {
  return (
    <div className="flex justify-end">
      <p className="max-w-[42rem] rounded-2xl rounded-br-sm bg-muted px-4 py-3 text-base whitespace-pre-line text-foreground">
        {message}
      </p>
    </div>
  )
}

function PendingTurn({ message }: { message: string }) {
  return (
    <div className="flex flex-col gap-4">
      <UserMessage message={message} />
      <div
        role="status"
        aria-busy="true"
        className="rounded-lg border border-border bg-card p-5 text-sm"
      >
        <p className="text-base font-bold text-primary">Working on your reply…</p>
        <p className="text-muted-foreground">
          A follow-up is rewritten with the conversation&apos;s context first, then the same
          deterministic pipeline as the search page runs. This can take several seconds.
        </p>
      </div>
    </div>
  )
}

interface PendingQuestionProps {
  pending: PendingOut
  /** A submit is in flight or a reply is being waited on: nothing more to choose. */
  busy: boolean
  onChoose: (option: ChatOptionOut) => void
}

/** The question a paused turn waits on, as the assistant's message with one button per
 * option. The buttons go away once the turn is answered (the chosen option then reads
 * as the user's message) or dropped (a new message handled as a normal turn). */
function PendingQuestion({ pending, busy, onChoose }: PendingQuestionProps) {
  return (
    <div
      role="status"
      aria-label="The assistant needs one detail"
      className="flex flex-col gap-3 rounded-lg border border-border bg-card p-5"
    >
      <p className="text-base font-bold text-primary">{pending.question}</p>
      {pending.reason !== null && (
        <p className="text-sm text-warning">
          That answer named none of the options — pick one of the places below, or send a new
          message to drop the question.
        </p>
      )}
      <div
        role="group"
        aria-label="The places the question could mean"
        className="flex flex-wrap gap-3"
      >
        {pending.options.map((option) => (
          <button
            key={option.id}
            type="button"
            disabled={busy}
            onClick={() => {
              onChoose(option)
            }}
            className="tap-target transition-colors-fast cursor-pointer rounded-md bg-accent px-4 font-bold text-on-accent hover:bg-primary disabled:cursor-not-allowed disabled:opacity-50"
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  )
}
