export type Tab = 'chat' | 'search'

/** The two tabs live in the URL hash; anything unrecognised is the default, chat. */
export function tabFromHash(hash: string): Tab {
  return hash === '#/search' ? 'search' : 'chat'
}
