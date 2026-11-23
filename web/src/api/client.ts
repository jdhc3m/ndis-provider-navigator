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
export type ChatOut = components['schemas']['ChatOut']
export type ChatThreadOut = components['schemas']['ChatThreadOut']
export type ChatTurnOut = components['schemas']['ChatTurnOut']

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

export interface NavigatorApi {
  meta: () => Promise<MetaOut>
  ask: (question: string) => Promise<AskOut>
  /** One conversation turn: `threadId` continues a thread (null starts one); the
   * answer names the thread to keep sending and the standalone question a follow-up
   * was understood as. */
  chat: (message: string, threadId: string | null) => Promise<ChatOut>
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
    chat: (message: string, threadId: string | null) => {
      // `thread_id` absent means "start a new thread"; present means "continue it".
      const body: RequestBody<'/chat', 'post'> =
        threadId === null ? { message } : { message, thread_id: threadId }
      return request('/chat', 'post', body)
    },
    getThread: (threadId: string) =>
      request('/chat/{thread_id}', 'get', null, `/chat/${encodeURIComponent(threadId)}`),
  }
}
