import { createClient, type NavigatorApi } from './client'

/**
 * The client the page uses. Mock mode exists only in builds made with
 * `VITE_MOCK_API=1`; Vite inlines the variable, so in every other build the branch is
 * `false` and the fixtures module is never bundled. Inside mock mode `?mock=answer`,
 * `refusal`, `empty` or `error` picks the scenario (default `answer`).
 */
export async function resolveClient(search: string): Promise<NavigatorApi> {
  if (import.meta.env.VITE_MOCK_API === '1') {
    const { createMockClient, isMockScenario } = await import('./mock')
    const requested = new URLSearchParams(search).get('mock')
    return createMockClient(isMockScenario(requested) ? requested : 'answer')
  }
  return createClient()
}
