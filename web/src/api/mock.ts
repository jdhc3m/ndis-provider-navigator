/**
 * Fixture-backed client for development and screenshots on machines without a model
 * endpoint. Only reachable when `VITE_MOCK_API=1` at build time (see `src/api/index.ts`);
 * a normal production build never includes this module.
 */
import { ApiError, type ChatOut, type ChatTurnOut, type NavigatorApi } from './client'
import {
  AMBIGUOUS,
  ANSWER,
  EMPTY,
  ERROR_DETAIL,
  ERROR_STATUS,
  LOCALITY,
  META,
  REFUSAL,
  REPLY_1,
  REPLY_2,
  REPLY_REFUSAL,
  SERVICE,
  THREAD_FULL_DETAIL,
  THREAD_NOT_FOUND_DETAIL,
  UNKNOWN_SERVICE,
} from './fixtures'

export const MOCK_SCENARIOS = [
  'answer',
  'refusal',
  'empty',
  'error',
  'locality',
  'ambiguous',
  'service',
  'unknown-service',
  'thread-not-found',
  'thread-full',
] as const
export type MockScenario = (typeof MOCK_SCENARIOS)[number]

export function isMockScenario(value: string | null): value is MockScenario {
  return (MOCK_SCENARIOS as readonly string[]).includes(value ?? '')
}

/** The replies one mock conversation produces, in order: the full answer, the rewritten
 * follow-up, then a refusal inside the conversation. A thread's turn count picks the
 * reply, so any message sent as the second turn is answered as the follow-up fixture. */
const CONVERSATION_REPLIES: readonly ChatOut[] = [REPLY_1, REPLY_2, REPLY_REFUSAL]

let mockThreadCounter = 0

export function createMockClient(scenario: MockScenario): NavigatorApi {
  // Per-client conversation state, like the server's in-memory checkpointer: the chat
  // tab keeps only the thread id, the mock holds the turns behind it.
  const threads = new Map<string, ChatTurnOut[]>()
  const chatLost = scenario === 'thread-not-found'
  const chatIsFull = scenario === 'thread-full'
  const chatErrors = scenario === 'error'
  return {
    meta: () => Promise.resolve(META),
    ask: (question: string) => {
      switch (scenario) {
        case 'answer':
          return Promise.resolve({ ...ANSWER, question })
        case 'refusal':
          return Promise.resolve({ ...REFUSAL, question })
        case 'empty':
          return Promise.resolve({ ...EMPTY, question })
        case 'locality':
          return Promise.resolve({ ...LOCALITY, question })
        case 'ambiguous':
          return Promise.resolve({ ...AMBIGUOUS, question })
        case 'service':
          return Promise.resolve({ ...SERVICE, question })
        case 'unknown-service':
          return Promise.resolve({ ...UNKNOWN_SERVICE, question })
        case 'error':
          return Promise.reject(new ApiError(ERROR_STATUS, ERROR_DETAIL))
        case 'thread-not-found':
        case 'thread-full':
          return Promise.resolve({ ...ANSWER, question })
      }
    },
    chat: (message: string, threadId: string | null) => {
      if (chatErrors) {
        return Promise.reject(new ApiError(ERROR_STATUS, ERROR_DETAIL))
      }
      if (chatLost) {
        return Promise.reject(new ApiError(404, { ...THREAD_NOT_FOUND_DETAIL }))
      }
      if (chatIsFull) {
        return Promise.reject(new ApiError(409, { ...THREAD_FULL_DETAIL }))
      }
      if (threadId !== null && !threads.has(threadId)) {
        return Promise.reject(new ApiError(404, { ...THREAD_NOT_FOUND_DETAIL }))
      }
      const id = threadId ?? `mock-thread-${String(++mockThreadCounter).padStart(3, '0')}`
      const turns = threads.get(id) ?? []
      const reply =
        CONVERSATION_REPLIES[Math.min(turns.length, CONVERSATION_REPLIES.length - 1)] ?? REPLY_1
      const turn: ChatTurnOut = {
        message,
        standalone_question: reply.standalone_question,
        rewritten: reply.rewritten,
        rewrite_reason: reply.rewrite_reason,
        answer: reply.answer,
      }
      threads.set(id, [...turns, turn])
      return Promise.resolve({ ...reply, thread_id: id })
    },
    getThread: (threadId: string) => {
      if (chatLost || !threads.has(threadId)) {
        return Promise.reject(new ApiError(404, { ...THREAD_NOT_FOUND_DETAIL }))
      }
      const turns = threads.get(threadId) ?? []
      return Promise.resolve({ thread_id: threadId, turns })
    },
  }
}
