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
export type AskIntentOut = components['schemas']['AskIntentOut']
export type ServiceMentionOut = components['schemas']['ServiceMentionOut']
export type PriceLimitOut = components['schemas']['PriceLimitOut']
export type AskRuleOut = components['schemas']['AskRuleOut']
export type StepOut = components['schemas']['StepOut']
export type MetaOut = components['schemas']['MetaOut']
export type ProviderPageOut = components['schemas']['ProviderPageOut']

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

/** What the API gives when a request fails: the HTTP status and FastAPI's `detail`. */
export class ApiError extends Error {
  readonly status: number
  readonly detail: string | null

  constructor(status: number, detail: string | null) {
    super(detail ?? `Request failed with status ${status}`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

/** Status 0 marks a network failure (no response at all). */
export const NETWORK_ERROR_STATUS = 0

export interface NavigatorApi {
  meta: () => Promise<MetaOut>
  ask: (question: string) => Promise<AskOut>
}

/** Same origin by default: that is where the FastAPI static mount serves the page from. */
export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? ''

type ValidationDetail = components['schemas']['HTTPValidationError']['detail']

function detailFrom(body: unknown): string | null {
  if (typeof body !== 'object' || body === null || !('detail' in body)) {
    return null
  }
  const detail: unknown = body.detail
  if (typeof detail === 'string') {
    return detail
  }
  if (Array.isArray(detail)) {
    return (detail as NonNullable<ValidationDetail>)
      .map((item) => `${item.loc.join('.')}: ${item.msg}`)
      .join('; ')
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
  ): Promise<Ok<P, M>> {
    let response: Response
    try {
      response = await fetchImpl(`${baseUrl}${path}`, {
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
  }
}
