/**
 * Fixture-backed client for development and screenshots on machines without a model
 * endpoint. Only reachable when `VITE_MOCK_API=1` at build time (see `src/api/index.ts`);
 * a normal production build never includes this module.
 */
import { ApiError, type ChatOut, type ChatTurnOut, type NavigatorApi } from './client'
import {
  AMBIGUOUS,
  AMBIGUOUS_MESSAGE,
  ANSWER,
  EMPTY,
  ERROR_DETAIL,
  ERROR_STATUS,
  LOCALITY,
  META,
  PENDING_LOCALITY,
  REFUSAL,
  REPLY_1,
  REPLY_2,
  REPLY_PENDING,
  REPLY_REFUSAL,
  REPLY_RESUMED,
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
  'pending',
] as const
export type MockScenario = (typeof MOCK_SCENARIOS)[number]

export function isMockScenario(value: string | null): value is MockScenario {
  return (MOCK_SCENARIOS as readonly string[]).includes(value ?? '')
}

/** The replies one mock conversation produces, in order: the full answer, the rewritten
 * follow-up, then a refusal inside the conversation. A thread's turn count picks the
 * reply, so any message sent as the second turn is answered as the follow-up fixture. */
const CONVERSATION_REPLIES: readonly ChatOut[] = [REPLY_1, REPLY_2, REPLY_REFUSAL]

/** The answer of a resumed turn: the fixture's answer is always set by construction. */
function resumedAnswer(): NonNullable<ChatOut['answer']> {
  const answer = REPLY_RESUMED.answer
  if (answer === null) {
    throw new Error('the resumed fixture carries no answer')
  }
  return answer
}

/** One stored turn from an answered reply; the mock replies always carry an answer. */
function answeredTurn(message: string, reply: ChatOut): ChatTurnOut {
  if (reply.answer === null || reply.standalone_question === null) {
    throw new Error('a mock reply answered without an answer')
  }
  return {
    message,
    standalone_question: reply.standalone_question,
    rewritten: reply.rewritten,
    rewrite_reason: reply.rewrite_reason,
    answer: reply.answer,
  }
}

/** What the resume endpoint answers when the thread waits for nothing. */
const NOTHING_PENDING_DETAIL = {
  reason: 'nothing_pending',
  message: 'this conversation is not waiting for an answer',
} as const

let mockThreadCounter = 0

export function createMockClient(scenario: MockScenario): NavigatorApi {
  // Per-client conversation state, like the server's in-memory checkpointer: the chat
  // tab keeps only the thread id, the mock holds the turns behind it.
  const threads = new Map<string, ChatTurnOut[]>()
  const chatLost = scenario === 'thread-not-found'
  const chatIsFull = scenario === 'thread-full'
  const chatErrors = scenario === 'error'
  const chatPauses = scenario === 'pending'
  // The threads of the pausing scenario that were already answered, with the option
  // that answered them, so a reload restores the completed turn.
  const paused = new Map<string, ChatTurnOut>()
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
        case 'pending':
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
      if (threadId !== null && !threads.has(threadId) && !paused.has(threadId)) {
        return Promise.reject(new ApiError(404, { ...THREAD_NOT_FOUND_DETAIL }))
      }
      const id = threadId ?? `mock-thread-${String(++mockThreadCounter).padStart(3, '0')}`
      if (chatPauses) {
        // The whole conversation is one paused turn: the first message asks which
        // Paddington the user meant, and only the resume stores the turn.
        return Promise.resolve({ ...REPLY_PENDING, thread_id: id })
      }
      const turns = threads.get(id) ?? []
      const reply =
        CONVERSATION_REPLIES[Math.min(turns.length, CONVERSATION_REPLIES.length - 1)] ?? REPLY_1
      const turn = answeredTurn(message, reply)
      threads.set(id, [...turns, turn])
      return Promise.resolve({ ...reply, thread_id: id })
    },
    resumeChat: (threadId: string, optionId: string) => {
      if (!chatPauses) {
        // Only the pausing scenario has a pending question; every other thread waits
        // for nothing, exactly as the API refuses.
        return Promise.reject(new ApiError(409, { ...NOTHING_PENDING_DETAIL }))
      }
      if (!threads.has(threadId) && !paused.has(threadId)) {
        return Promise.reject(new ApiError(404, { ...THREAD_NOT_FOUND_DETAIL }))
      }
      const [stateCode, postcode] = optionId.split('-')
      const state = (stateCode ?? 'qld').toUpperCase()
      const turn: ChatTurnOut = {
        message: state === 'NSW' ? 'NSW 2021' : `QLD ${postcode ?? '4064'}`,
        standalone_question: REPLY_RESUMED.standalone_question ?? AMBIGUOUS_MESSAGE,
        rewritten: false,
        rewrite_reason: null,
        answer: {
          ...resumedAnswer(),
          locality: {
            name: 'Paddington',
            outcome: 'resolved',
            state,
            postcode: postcode ?? '4064',
            candidates: [],
          },
        },
      }
      paused.set(threadId, turn)
      return Promise.resolve({ ...REPLY_RESUMED, thread_id: threadId, answer: turn.answer })
    },
    getThread: (threadId: string) => {
      if (chatLost || (!threads.has(threadId) && !paused.has(threadId))) {
        return Promise.reject(new ApiError(404, { ...THREAD_NOT_FOUND_DETAIL }))
      }
      if (chatPauses) {
        const answered = paused.get(threadId)
        return Promise.resolve({
          thread_id: threadId,
          turns: answered ? [answered] : [],
          pending: answered ? null : PENDING_LOCALITY,
        })
      }
      const turns = threads.get(threadId) ?? []
      return Promise.resolve({ thread_id: threadId, turns, pending: null })
    },
  }
}
