import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { resolveClient } from './api'
import { App } from './App'
import './index.css'

const root = document.getElementById('root')
if (root === null) {
  throw new Error('index.html has no #root element')
}

const initialQuestion = new URLSearchParams(window.location.search).get('q')

resolveClient(window.location.search).then(
  (client) => {
    createRoot(root).render(
      <StrictMode>
        <App client={client} {...(initialQuestion ? { initialQuestion } : {})} />
      </StrictMode>,
    )
  },
  (error: unknown) => {
    root.textContent = `The page could not start: ${error instanceof Error ? error.message : String(error)}`
  },
)
