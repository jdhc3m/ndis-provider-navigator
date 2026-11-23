import { useEffect, useState } from 'react'

import type { MetaOut, NavigatorApi } from './api/client'
import { ChatPage } from './components/ChatPage'
import { Footer } from './components/Footer'
import { Header } from './components/Header'
import { SearchPage } from './components/SearchPage'
import { tabFromHash, type Tab } from './tabs'

interface AppProps {
  client: NavigatorApi
  /** A question from the `?q=` link parameter: lands on the search tab and asked once
   * on load there — a deep link into the search page. */
  initialQuestion?: string
}

export function App({ client, initialQuestion }: AppProps) {
  const startQuestion = initialQuestion?.trim() ?? ''
  const [meta, setMeta] = useState<MetaOut | null>(null)
  const [metaError, setMetaError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>(() =>
    startQuestion ? 'search' : tabFromHash(window.location.hash),
  )

  useEffect(() => {
    let cancelled = false
    client.meta().then(
      (value) => {
        if (!cancelled) setMeta(value)
      },
      (error: unknown) => {
        if (!cancelled) {
          setMetaError(error instanceof Error ? error.message : 'unknown error')
        }
      },
    )
    return () => {
      cancelled = true
    }
  }, [client])

  useEffect(() => {
    const onHashChange = () => {
      setTab(tabFromHash(window.location.hash))
    }
    window.addEventListener('hashchange', onHashChange)
    return () => {
      window.removeEventListener('hashchange', onHashChange)
    }
  }, [])

  return (
    <div className="flex min-h-dvh flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-10 focus:rounded-md focus:bg-primary focus:px-4 focus:py-3 focus:text-on-primary"
      >
        Skip to main content
      </a>
      <Header meta={meta} metaError={metaError} />
      <nav aria-label="Page sections" className="bg-card">
        <div className="mx-auto flex max-w-6xl gap-1 px-4 sm:px-6">
          <TabLink
            tab="chat"
            active={tab === 'chat'}
            onNavigate={() => {
              setTab('chat')
            }}
          >
            Chat
          </TabLink>
          <TabLink
            tab="search"
            active={tab === 'search'}
            onNavigate={() => {
              setTab('search')
            }}
          >
            Search
          </TabLink>
        </div>
      </nav>
      <main
        id="main"
        className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-8 px-4 py-8 sm:px-6"
      >
        {/* Both tabs stay mounted so a switch neither loses the search result nor re-asks
          the `?q=` deep link; the hidden one is out of the tab order and the a11y tree. */}
        <div hidden={tab !== 'chat'} className="flex flex-col gap-8">
          <ChatPage client={client} meta={meta} />
        </div>
        <div hidden={tab !== 'search'} className="flex flex-col gap-8">
          <SearchPage client={client} meta={meta} initialQuestion={startQuestion} />
        </div>
      </main>
      <Footer meta={meta} />
    </div>
  )
}

interface TabLinkProps {
  tab: Tab
  active: boolean
  onNavigate: () => void
  children: string
}

function TabLink({ tab, active, onNavigate, children }: TabLinkProps) {
  return (
    <a
      href={`#/${tab}`}
      aria-current={active ? 'page' : undefined}
      onClick={onNavigate}
      className={`tap-target transition-colors-fast inline-flex cursor-pointer items-center border-b-2 px-4 text-sm font-bold ${
        active
          ? 'border-accent text-accent'
          : 'border-transparent text-muted-foreground hover:border-border hover:text-foreground'
      }`}
    >
      {children}
    </a>
  )
}
