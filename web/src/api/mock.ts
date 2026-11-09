/**
 * Fixture-backed client for development and screenshots on machines without a model
 * endpoint. Only reachable when `VITE_MOCK_API=1` at build time (see `src/api/index.ts`);
 * a normal production build never includes this module.
 */
import { ApiError, type NavigatorApi } from './client'
import { ANSWER, EMPTY, ERROR_DETAIL, ERROR_STATUS, META, REFUSAL } from './fixtures'

export const MOCK_SCENARIOS = ['answer', 'refusal', 'empty', 'error'] as const
export type MockScenario = (typeof MOCK_SCENARIOS)[number]

export function isMockScenario(value: string | null): value is MockScenario {
  return (MOCK_SCENARIOS as readonly string[]).includes(value ?? '')
}

export function createMockClient(scenario: MockScenario): NavigatorApi {
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
        case 'error':
          return Promise.reject(new ApiError(ERROR_STATUS, ERROR_DETAIL))
      }
    },
  }
}
