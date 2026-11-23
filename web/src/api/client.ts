/**
 * A thin typed fetch client over the generated OpenAPI `paths` type.
 *
 * The response and body types come from `schema.d.ts` (regenerate with
 * `pnpm gen:schema` after `uv run python scripts/export_openapi.py openapi.json`), so a
 * backend change that the page does not handle fails `pnpm typecheck` instead of
 * surfacing at runtime. Errors become `ApiError` carrying FastAPI's `detail` string.
 */
import type { components, paths } from './schema'

export type AskOut = components['schemas']['AskOut']
export type AskProviderOut = components['schemas']['AskProviderOut']
export type DistanceOut = components['schemas']['DistanceOut']
export type AskIntentOut = components['schemas']['AskIntentOut']
export type ServiceMentionOut = components['schemas']['ServiceMentionOut']
export type PriceLimitOut = components['schemas']['PriceLimitOut']
export type ServicePricesOut = components['schemas']['ServicePricesOut']
export type ServicePriceOut = components['schemas']['ServicePriceOut']
export type AskRuleOut = components['schemas']['AskRuleOut']
export type StepOut = components['schemas']['StepOut']
export type MetaOut = components['schemas']['MetaOut']
export type ProviderPageOut = components['schemas']['ProviderPageOut']
export type LocalityOut = components['schemas']['LocalityOut']
export type LocalityCandidateOut = components['schemas']['LocalityCandidateOut']
export type ChatTurnOut = components['schemas']['ChatTurnOut']

/** The pending question a paused turn waits on: which place the user meant, one option
 * per candidate, plus the reason when an answer named none of them. Strictly nullable:
 * the API always sends every field (null when unset), so the page never sees undefined. */
export interface PendingOut {
  kind: string
  question: string
  options: ChatOptionOut[]
  reason: string | null
}

/** One offered answer: the stable id to send back on resume, and the label the button
 * shows. */
export interface ChatOptionOut {
  id: string
  label: string
}

/** One turn's result, with the unset fields spelled null: `needs_input` carries the
 * pending question and no answer; `answered` carries the answer and no pending. */
export interface ChatOut {
  thread_id: string
  status: 'answered' | 'needs_input'
  standalone_question: string | null
  rewritten: boolean
  rewrite_reason: string | null
  answer: AskOut | null
  pending: PendingOut | null
}

/** A whole stored conversation, with the pending question when a turn is paused. */
export interface ChatThreadOut {
  thread_id: string
  turns: ChatTurnOut[]
  pending: PendingOut | null
}

type JsonBody<T> = T extends { content: { 'application/json': infer B } } ? B : never
type Ok<P extends keyof paths, M extends keyof paths[P]> = paths[P][M] extends {
  responses: { 200: infer R }
}
  ? JsonBody<R>
  : never
type RequestBody<P extends keyof paths, M extends keyof paths[P]> = paths[P][M] extends {
  requestBody: infer R
}
  ? JsonBody<R>
  : never

/** What the API gives when a request fails: the HTTP status and FastAPI's `detail` — a
 * string, or a structured object (the chat endpoints send `reason` plus a readable
 * `message`, which drive the page's notices). */
export class ApiError extends Error {
  readonly status: number
  readonly detail: string | null
  /** The machine reason of a structured detail, e.g. `thread_not_found`. */
  readonly reason: string | null

  constructor(status: number, detail: string | Record<string, unknown> | null) {
    super(structuredDetailText(detail) ?? `Request failed with status ${status}`)
    this.name = 'ApiError'
    this.status = status
    this.detail = structuredDetailText(detail)
    this.reason = structuredDetailReason(detail)
  }
}

function structuredDetailText(detail: unknown): string | null {
  if (typeof detail === 'string') {
    return detail
  }
  if (Array.isArray(detail)) {
    return (detail as NonNullable<ValidationDetail>)
      .map((item) => `${item.loc.join('.')}: ${item.msg}`)
      .join('; ')
  }
  if (
    typeof detail === 'object' &&
    detail !== null &&
    'message' in detail &&
    typeof detail.message === 'string'
  ) {
    return detail.message
  }
  return null
}

function structuredDetailReason(detail: unknown): string | null {
  if (
    typeof detail === 'object' &&
    detail !== null &&
    !Array.isArray(detail) &&
    'reason' in detail &&
    typeof detail.reason === 'string'
  ) {
    return detail.reason
  }
  return null
}

/** Status 0 marks a network failure (no response at all). */
export const NETWORK_ERROR_STATUS = 0

/** The wire shape of one turn's result, before the unset fields are spelled null. */
type WireChatOut = components['schemas']['ChatOut']
type WireChatThreadOut = components['schemas']['ChatThreadOut']

function pendingOf(pending: WireChatOut['pending']): PendingOut | null {
  return pending === null || pending === undefined
    ? null
    : { ...pending, reason: pending.reason ?? null }
}

function chatOutOf(reply: WireChatOut): ChatOut {
  return {
    thread_id: reply.thread_id,
    status: reply.status,
    standalone_question: reply.standalone_question ?? null,
    rewritten: reply.rewritten,
    rewrite_reason: reply.rewrite_reason ?? null,
    answer: reply.answer ?? null,
    pending: pendingOf(reply.pending),
  }
}

function threadOutOf(thread: WireChatThreadOut): ChatThreadOut {
  return {
    thread_id: thread.thread_id,
    turns: thread.turns,
    pending: pendingOf(thread.pending),
  }
}

export interface NavigatorApi {
  meta: () => Promise<MetaOut>
  ask: (question: string) => Promise<AskOut>
  /** One conversation turn: `threadId` continues a thread (null starts one); the
   * answer names the thread to keep sending and the standalone question a follow-up
   * was understood as. A `needs_input` status carries the pending question instead of
   * an answer. */
  chat: (message: string, threadId: string | null) => Promise<ChatOut>
  /** The answer to a thread's pending question: the chosen option's id exactly as the
   * pending question offered it; the same turn carries on and returns like `chat`. */
  resumeChat: (threadId: string, optionId: string) => Promise<ChatOut>
  /** The turns of one stored conversation, for restoring a page after a reload. */
  getThread: (threadId: string) => Promise<ChatThreadOut>
}

/** Same origin by default: that is where the FastAPI static mount serves the page from. */
export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? ''

type ValidationDetail = components['schemas']['HTTPValidationError']['detail']

function detailFrom(body: unknown): string | Record<string, unknown> | null {
  if (typeof body !== 'object' || body === null || !('detail' in body)) {
    return null
  }
  const detail: unknown = body.detail
  if (typeof detail === 'string') {
    return detail
  }
  if (Array.isArray(detail)) {
    return structuredDetailText(detail)
  }
  if (typeof detail === 'object' && detail !== null) {
    return detail as Record<string, unknown>
  }
  return null
}

export function createClient(
  baseUrl: string = API_BASE_URL,
  fetchImpl: typeof fetch = (...args) => fetch(...args),
): NavigatorApi {
  async function request<P extends keyof paths, M extends keyof paths[P] & string>(
    path: P,
    method: M,
    // Typed at the call sites (see `ask`): the deferred conditional type cannot be
    // narrowed inside this generic body.
    body: unknown,
    // Overrides the templated path when it carries a parameter (see `getThread`).
    url?: string,
  ): Promise<Ok<P, M>> {
    let response: Response
    try {
      response = await fetchImpl(`${baseUrl}${url ?? path}`, {
        method: method.toUpperCase(),
        headers: body === null ? {} : { 'Content-Type': 'application/json' },
        body: body === null ? null : JSON.stringify(body),
      })
    } catch (error) {
      throw new ApiError(
        NETWORK_ERROR_STATUS,
        error instanceof Error ? error.message : 'network request failed',
      )
    }
    // A failed response may carry no JSON at all; `detail` is then simply absent.
    const payload: unknown = await response.json().catch(() => null)
    if (!response.ok) {
      throw new ApiError(response.status, detailFrom(payload))
    }
    return payload as Ok<P, M>
  }

  return {
    meta: () => request('/meta', 'get', null),
    ask: (question: string) => {
      const body: RequestBody<'/ask', 'post'> = { question }
      return request('/ask', 'post', body)
    },
    chat: async (message: string, threadId: string | null) => {
      // `thread_id` absent means "start a new thread"; present means "continue it".
      const body: RequestBody<'/chat', 'post'> =
        threadId === null ? { message } : { message, thread_id: threadId }
      return chatOutOf(await request('/chat', 'post', body))
    },
    resumeChat: async (threadId: string, optionId: string) => {
      const body: RequestBody<'/chat/{thread_id}/resume', 'post'> = { option_id: optionId }
      return chatOutOf(
        await request(
          '/chat/{thread_id}/resume',
          'post',
          body,
          `/chat/${encodeURIComponent(threadId)}/resume`,
        ),
      )
    },
    getThread: async (threadId: string) =>
      threadOutOf(
        await request('/chat/{thread_id}', 'get', null, `/chat/${encodeURIComponent(threadId)}`),
      ),
  }
}
