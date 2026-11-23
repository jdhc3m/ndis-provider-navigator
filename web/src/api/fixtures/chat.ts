import type { ChatOut, ChatThreadOut, ChatTurnOut, PendingOut } from '../client'
import { ANSWER } from './answer'
import { LOCALITY } from './locality'
import { REFUSAL } from './refusal'

/** The thread id the mock conversation lives under (a UUID, as the server issues). */
export const THREAD_ID = 'f3d40257-8a52-4a3b-9f7e-2f6a1b9c8d01'

/** The first message of the mock conversation and the standalone question a follow-up
 * was rewritten into; the answers reuse the search fixtures (fictitious data). */
export const FIRST_MESSAGE = 'Which providers in postcode 4101 offer speech pathology?'
export const FOLLOW_UP_MESSAGE = 'and in South Bank?'
export const STANDALONE_QUESTION = 'Which providers in South Bank, QLD offer speech pathology?'

/** One stored conversation turn, the shape `GET /chat/{thread_id}` returns. */
export const TURN_1: ChatTurnOut = {
  message: FIRST_MESSAGE,
  standalone_question: FIRST_MESSAGE,
  rewritten: false,
  rewrite_reason: null,
  answer: ANSWER,
}

/** The follow-up turn: the reply discloses the standalone question it was understood as. */
export const TURN_2: ChatTurnOut = {
  message: FOLLOW_UP_MESSAGE,
  standalone_question: STANDALONE_QUESTION,
  rewritten: true,
  rewrite_reason: null,
  answer: { ...LOCALITY, question: STANDALONE_QUESTION },
}

/** The two-turn conversation the mock client restores and continues. */
export const THREAD: ChatThreadOut = {
  thread_id: THREAD_ID,
  turns: [TURN_1, TURN_2],
  pending: null,
}

/** The mock replies, in the order the conversation's turns produce them; the third
 * message is refused, so the chat tab can show a refusal inside a conversation. */
export const REPLY_1: ChatOut = {
  thread_id: THREAD_ID,
  status: 'answered',
  standalone_question: TURN_1.standalone_question,
  rewritten: TURN_1.rewritten,
  rewrite_reason: TURN_1.rewrite_reason,
  answer: TURN_1.answer,
  pending: null,
}

export const REPLY_2: ChatOut = {
  thread_id: THREAD_ID,
  status: 'answered',
  standalone_question: TURN_2.standalone_question,
  rewritten: TURN_2.rewritten,
  rewrite_reason: TURN_2.rewrite_reason,
  answer: TURN_2.answer,
  pending: null,
}

export const REPLY_REFUSAL: ChatOut = {
  thread_id: THREAD_ID,
  status: 'answered',
  standalone_question: REFUSAL.question,
  rewritten: false,
  rewrite_reason: null,
  answer: REFUSAL,
  pending: null,
}

/** The message the paused turn asked: "Paddington" matches a suburb in QLD and one in
 * NSW, so the graph stops and asks which one the user meant. */
export const AMBIGUOUS_MESSAGE = 'plan managers in Paddington'

/** The pending question the paused turn waits on (fixture data: the real gazetteer
 * spells Paddington twice). */
export const PENDING_LOCALITY: PendingOut = {
  kind: 'choose_locality',
  question: 'Which "Paddington" do you mean?',
  options: [
    { id: 'nsw-2021', label: 'NSW 2021' },
    { id: 'qld-4064', label: 'QLD 4064' },
  ],
  reason: null,
}

/** The same question asked again after an answer that named none of the options. */
export const PENDING_REASK: PendingOut = { ...PENDING_LOCALITY, reason: 'invalid_option' }

/** What `POST /chat` returns when the turn stopped to ask which place the user meant. */
export const REPLY_PENDING: ChatOut = {
  thread_id: THREAD_ID,
  status: 'needs_input',
  standalone_question: AMBIGUOUS_MESSAGE,
  rewritten: false,
  rewrite_reason: null,
  answer: null,
  pending: PENDING_LOCALITY,
}

/** What resuming with the NSW option returns: the same turn, now answered. */
export const REPLY_RESUMED: ChatOut = {
  thread_id: THREAD_ID,
  status: 'answered',
  standalone_question: AMBIGUOUS_MESSAGE,
  rewritten: false,
  rewrite_reason: null,
  answer: {
    ...LOCALITY,
    question: AMBIGUOUS_MESSAGE,
    locality: {
      name: 'Paddington',
      outcome: 'resolved',
      state: 'NSW',
      postcode: '2021',
      candidates: [],
    },
  },
  pending: null,
}

/** What the server sends when a thread id is not in its memory (a restart lost it). */
export const THREAD_NOT_FOUND_DETAIL = {
  reason: 'thread_not_found',
  message:
    "that conversation is not in the server's memory; it may have been lost in a restart — start a new one",
} as const

/** What the server sends when a thread holds its maximum number of turns. */
export const THREAD_FULL_DETAIL = {
  reason: 'thread_full',
  message: 'this conversation holds its maximum of 50 turns; start a new one',
} as const
