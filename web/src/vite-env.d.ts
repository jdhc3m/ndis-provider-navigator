/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** API origin; empty (same origin) in the built page, `http://localhost:8000` in dev. */
  readonly VITE_API_BASE_URL?: string
  /** `'1'` serves the fixtures in `src/api/fixtures/` instead of calling the API. */
  readonly VITE_MOCK_API?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
