import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from './App'
import { tabFromHash } from './tabs'
import { ApiError, type ChatOut, type ChatThreadOut, type NavigatorApi } from './api/client'
import {
  ANSWER,
  EMPTY,
  META,
  REPLY_1,
  REPLY_2,
  REPLY_REFUSAL,
  STANDALONE_QUESTION,
  THREAD,
  THREAD_ID,
} from './api/fixtures'
import { CHAT_EXAMPLE_QUESTIONS } from './examples'

const QUESTION = 'Which providers in postcode 4101 offer speech pathology?'

/** A client that never touches the network: every call resolves from the fixtures. */
function fakeClient(overrides: Partial<NavigatorApi> = {}): NavigatorApi {
  return {
    meta: vi.fn(() => Promise.resolve(META)),
    ask: vi.fn(() => Promise.resolve(ANSWER)),
    chat: vi.fn(() => Promise.resolve(REPLY_1)),
    getThread: vi.fn(() => Promise.resolve(THREAD)),
    ...overrides,
  }
}

const NOT_FOUND = new ApiError(404, {
  reason: 'thread_not_found',
  message: "that conversation is not in the server's memory (test)",
})
const FULL = new ApiError(409, { reason: 'thread_full', message: 'holds 50 turns (test)' })
const FRESH_THREAD_ID = '0e0b7e40-6f0e-4a52-9c5e-6f4a8f0f1a02'

describe('App', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })
  afterEach(() => {
    window.location.hash = ''
  })

  it('shows the snapshot date, catalogue, pricing document and model from /meta', async () => {
    render(<App client={fakeClient()} />)
    const header = await screen.findByRole('banner')
    expect(within(header).getByText('9 Nov 2026')).toBeInTheDocument()
    expect(within(header).getByText(META.catalogue_title)).toBeInTheDocument()
    expect(within(header).getByText(META.pricing_document)).toBeInTheDocument()
    expect(within(header).getByText(META.llm_model)).toBeInTheDocument()
  })

  describe('tabs', () => {
    it('lands on the chat tab by default, with the composer and the example chips', () => {
      render(<App client={fakeClient()} />)
      expect(screen.getByLabelText(/your message/i)).toBeInTheDocument()
      expect(
        screen.getByRole('button', { name: CHAT_EXAMPLE_QUESTIONS[0] ?? '' }),
      ).toBeInTheDocument()
      // The search tab is mounted but hidden: out of the tab order and the a11y tree.
      expect(screen.getByLabelText(/ask about ndis providers/i)).not.toBeVisible()
      const chatLink = screen.getByRole('link', { name: 'Chat' })
      expect(chatLink).toHaveAttribute('aria-current', 'page')
      expect(screen.getByRole('link', { name: 'Search' })).not.toHaveAttribute('aria-current')
    })

    it('switches to the search tab and back through the links', async () => {
      const user = userEvent.setup()
      render(<App client={fakeClient()} />)
      await user.click(screen.getByRole('link', { name: 'Search' }))
      expect(screen.getByLabelText(/ask about ndis providers/i)).toBeInTheDocument()
      expect(window.location.hash).toBe('#/search')
      expect(screen.getByRole('link', { name: 'Search' })).toHaveAttribute('aria-current', 'page')

      await user.click(screen.getByRole('link', { name: 'Chat' }))
      expect(screen.getByLabelText(/your message/i)).toBeInTheDocument()
      expect(window.location.hash).toBe('#/chat')
    })

    it('lands on the search tab when the hash says so, and on chat for anything else', () => {
      window.location.hash = '#/search'
      const { unmount } = render(<App client={fakeClient()} />)
      expect(screen.getByLabelText(/ask about ndis providers/i)).toBeInTheDocument()
      unmount()

      window.location.hash = '#/nonsense'
      render(<App client={fakeClient()} />)
      expect(screen.getByLabelText(/your message/i)).toBeInTheDocument()
    })

    it('reads #/search and #/chat and defaults everything else to chat', () => {
      expect(tabFromHash('#/search')).toBe('search')
      expect(tabFromHash('#/chat')).toBe('chat')
      expect(tabFromHash('')).toBe('chat')
      expect(tabFromHash('#/nope')).toBe('chat')
    })

    it('works by keyboard alone: tab to the composer, send, read, open evidence, switch tabs', async () => {
      const user = userEvent.setup()
      const chat = vi.fn<NavigatorApi['chat']>().mockResolvedValue(REPLY_1)
      render(<App client={fakeClient({ chat })} />)

      await user.tab()
      expect(screen.getByRole('link', { name: /skip to main content/i })).toHaveFocus()
      await user.tab()
      expect(screen.getByRole('link', { name: 'Chat' })).toHaveFocus()
      await user.tab()
      expect(screen.getByRole('link', { name: 'Search' })).toHaveFocus()
      await user.tab()
      const composer = screen.getByLabelText<HTMLTextAreaElement>(/your message/i)
      expect(composer).toHaveFocus()

      await user.type(composer, QUESTION)
      await user.keyboard('{Enter}')
      expect(await screen.findByText('Example Therapy Pty Ltd')).toBeInTheDocument()
      expect(chat).toHaveBeenCalledWith(QUESTION, null)
      expect(composer).toHaveFocus()

      const evidence = screen.getByRole('button', { name: 'Speech pathology evidence' })
      evidence.focus()
      await user.keyboard('{Enter}')
      expect(evidence).toHaveAttribute('aria-expanded', 'true')
      expect(screen.getByText(/our speech pathologists work/i)).toBeInTheDocument()

      screen.getByRole('link', { name: 'Search' }).focus()
      await user.keyboard('{Enter}')
      expect(screen.getByLabelText(/ask about ndis providers/i)).toBeInTheDocument()
    })
  })

  describe('conversation', () => {
    it('sends a first message, shows the reply and stores the returned thread id', async () => {
      const user = userEvent.setup()
      const chat = vi.fn<NavigatorApi['chat']>().mockResolvedValue(REPLY_1)
      render(<App client={fakeClient({ chat })} />)
      const composer = screen.getByLabelText<HTMLTextAreaElement>(/your message/i)
      await user.type(composer, QUESTION)
      await user.keyboard('{Enter}')

      expect(await screen.findByText('Example Therapy Pty Ltd')).toBeInTheDocument()
      expect(chat).toHaveBeenCalledWith(QUESTION, null)
      expect(window.localStorage.getItem('ndis-provider-navigator-thread')).toBe(REPLY_1.thread_id)
      // The reply returns focus to the composer, and the chips disappear after a message.
      expect(composer).toHaveFocus()
      expect(screen.queryByRole('button', { name: CHAT_EXAMPLE_QUESTIONS[0] ?? '' })).toBeNull()
    })

    it('adds a line on Shift+Enter and only sends on plain Enter', async () => {
      const user = userEvent.setup()
      const chat = vi.fn<NavigatorApi['chat']>().mockResolvedValue(REPLY_1)
      render(<App client={fakeClient({ chat })} />)
      const composer = screen.getByLabelText<HTMLTextAreaElement>(/your message/i)
      await user.type(composer, 'first line')
      await user.keyboard('{Shift>}{Enter}{/Shift}')
      await user.type(composer, 'second line')
      expect(composer.value).toBe('first line\nsecond line')
      expect(chat).not.toHaveBeenCalled()
      await user.keyboard('{Enter}')
      expect(await screen.findByText('Example Therapy Pty Ltd')).toBeInTheDocument()
      expect(chat).toHaveBeenCalledTimes(1)
    })

    it('restores two turns from the server on load; the browser keeps only the id', async () => {
      window.localStorage.setItem('ndis-provider-navigator-thread', THREAD_ID)
      const getThread = vi.fn<NavigatorApi['getThread']>().mockResolvedValue(THREAD)
      render(<App client={fakeClient({ getThread })} />)

      expect(getThread).toHaveBeenCalledWith(THREAD_ID)
      // Example Therapy answers both turns; Placeholder appears only in the first.
      expect((await screen.findAllByText('Example Therapy Pty Ltd')).length).toBeGreaterThanOrEqual(
        2,
      )
      expect(screen.getByText('Placeholder Supports Ltd')).toBeInTheDocument()
      // The follow-up's reply discloses the standalone question it was understood as.
      const understood = screen.getByText(/follow-up understood as/i)
      expect(understood).toHaveTextContent(STANDALONE_QUESTION)
      expect(screen.getByText(/locality: South Bank → QLD 4101/)).toBeInTheDocument()
      // The second message is rendered as the user's own words.
      expect(screen.getByText('and in South Bank?')).toBeInTheDocument()
      expect(window.localStorage.getItem('ndis-provider-navigator-thread')).toBe(THREAD_ID)
    })

    it('shows a restored refusal turn inside the conversation', async () => {
      const user = userEvent.setup()
      const chat = vi.fn<NavigatorApi['chat']>().mockResolvedValue(REPLY_REFUSAL)
      render(<App client={fakeClient({ chat })} />)
      const composer = screen.getByLabelText<HTMLTextAreaElement>(/your message/i)
      await user.type(composer, 'Which provider is best for my child?')
      await user.keyboard('{Enter}')
      const banner = await screen.findByRole('status', { name: 'Not answered' })
      expect(within(banner).getByText(/decision_request/)).toBeInTheDocument()
      expect(within(banner).getByText(/does not make recommendations/i)).toBeInTheDocument()
      expect(
        screen.getByRole('button', { name: /how this answer was built \(1 step/i }),
      ).toBeVisible()
    })

    it('shows a restored turn with zero matched providers', async () => {
      window.localStorage.setItem('ndis-provider-navigator-thread', THREAD_ID)
      const turn: ChatThreadOut['turns'][number] = {
        message: 'Which approved providers in postcode 4999 offer speech pathology?',
        standalone_question: 'Which approved providers in postcode 4999 offer speech pathology?',
        rewritten: false,
        rewrite_reason: null,
        answer: EMPTY,
      }
      const getThread = vi
        .fn<NavigatorApi['getThread']>()
        .mockResolvedValue({ thread_id: THREAD_ID, turns: [turn] })
      render(<App client={fakeClient({ getThread })} />)
      expect(await screen.findByText('0 matched')).toBeInTheDocument()
      expect(screen.getByText('postcode: 4999', { selector: 'span *' })).toBeInTheDocument()
    })

    it('retries a failed send and keeps the message as the draft', async () => {
      const user = userEvent.setup()
      const chat = vi
        .fn<NavigatorApi['chat']>()
        .mockRejectedValueOnce(new ApiError(502, 'intent parse failed: endpoint down (test)'))
        .mockResolvedValueOnce(REPLY_1)
      render(<App client={fakeClient({ chat })} />)
      const composer = screen.getByLabelText<HTMLTextAreaElement>(/your message/i)
      await user.type(composer, QUESTION)
      await user.keyboard('{Enter}')

      const alert = await screen.findByRole('alert')
      expect(
        within(alert).getByText('intent parse failed: endpoint down (test)'),
      ).toBeInTheDocument()
      expect(composer.value).toBe(QUESTION)
      expect(window.localStorage.getItem('ndis-provider-navigator-thread')).toBeNull()

      await user.click(within(alert).getByRole('button', { name: /try again/i }))
      expect(await screen.findByText('Example Therapy Pty Ltd')).toBeInTheDocument()
      expect(chat).toHaveBeenNthCalledWith(2, QUESTION, null)
      expect(window.localStorage.getItem('ndis-provider-navigator-thread')).toBe(REPLY_1.thread_id)
    })

    it('shows the not-kept notice and a fresh thread when the stored thread is unknown', async () => {
      window.localStorage.setItem('ndis-provider-navigator-thread', THREAD_ID)
      const getThread = vi.fn<NavigatorApi['getThread']>().mockRejectedValue(NOT_FOUND)
      const chat = vi.fn<NavigatorApi['chat']>().mockResolvedValue(REPLY_1)
      render(<App client={fakeClient({ getThread, chat })} />)

      const notice = await screen.findByRole('status', { name: 'Conversation not kept' })
      expect(within(notice).getByText(/no longer on the server/i)).toBeInTheDocument()
      expect(window.localStorage.getItem('ndis-provider-navigator-thread')).toBeNull()
      expect(
        screen.getByRole('button', { name: CHAT_EXAMPLE_QUESTIONS[0] ?? '' }),
      ).toBeInTheDocument()
      const composer = screen.getByLabelText<HTMLTextAreaElement>(/your message/i)
      await userEvent.setup().type(composer, QUESTION)
      await userEvent.setup().keyboard('{Enter}')
      expect(await screen.findByText('Example Therapy Pty Ltd')).toBeInTheDocument()
      expect(chat).toHaveBeenCalledWith(QUESTION, null)
    })

    it('shows the not-kept notice mid-conversation and keeps the message as the draft', async () => {
      const user = userEvent.setup()
      const chat = vi
        .fn<NavigatorApi['chat']>()
        .mockRejectedValueOnce(NOT_FOUND)
        .mockResolvedValueOnce(REPLY_1)
      render(<App client={fakeClient({ chat })} />)
      const composer = screen.getByLabelText<HTMLTextAreaElement>(/your message/i)
      await user.type(composer, QUESTION)
      await user.keyboard('{Enter}')

      const notice = await screen.findByRole('status', { name: 'Conversation not kept' })
      expect(within(notice).getByText(/no longer on the server/i)).toBeInTheDocument()
      expect(composer.value).toBe(QUESTION)
      expect(window.localStorage.getItem('ndis-provider-navigator-thread')).toBeNull()
      await user.keyboard('{Enter}')
      expect(await screen.findByText('Example Therapy Pty Ltd')).toBeInTheDocument()
      expect(chat).toHaveBeenNthCalledWith(2, QUESTION, null)
      expect(notice).not.toBeInTheDocument()
    })

    it('shows the thread-full notice, disables the composer and offers New chat', async () => {
      const user = userEvent.setup()
      const chat = vi.fn<NavigatorApi['chat']>().mockRejectedValue(FULL)
      render(<App client={fakeClient({ chat })} />)
      const composer = screen.getByLabelText<HTMLTextAreaElement>(/your message/i)
      await user.type(composer, 'one more question for the full thread?')
      await user.keyboard('{Enter}')

      const alert = await screen.findByRole('alert')
      expect(within(alert).getByText('holds 50 turns (test)')).toBeInTheDocument()
      expect(composer).toBeDisabled()
      expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()

      await user.click(within(alert).getByRole('button', { name: 'New chat' }))
      expect(screen.queryByRole('alert')).toBeNull()
      expect(composer).toBeEnabled()
      expect(composer.value).toBe('')
      expect(
        screen.getByRole('button', { name: CHAT_EXAMPLE_QUESTIONS[0] ?? '' }),
      ).toBeInTheDocument()
    })

    it('discards a stale reply when a new chat starts while a reply is pending', async () => {
      const user = userEvent.setup()
      let resolveStale: (reply: ChatOut) => void = () => undefined
      // The stale reply belongs to the thread the user is abandoning; the fresh one to
      // the conversation the next message starts.
      const stale: ChatOut = { ...REPLY_2, thread_id: THREAD_ID }
      const fresh: ChatOut = { ...REPLY_1, thread_id: FRESH_THREAD_ID }
      const chat = vi
        .fn<NavigatorApi['chat']>()
        .mockResolvedValueOnce(REPLY_1)
        .mockImplementationOnce(
          () =>
            new Promise<ChatOut>((resolve) => {
              resolveStale = resolve
            }),
        )
        .mockResolvedValueOnce(fresh)
      render(<App client={fakeClient({ chat })} />)
      const composer = screen.getByLabelText<HTMLTextAreaElement>(/your message/i)

      await user.type(composer, QUESTION)
      await user.keyboard('{Enter}')
      await screen.findByText('Example Therapy Pty Ltd')
      expect(window.localStorage.getItem('ndis-provider-navigator-thread')).toBe(REPLY_1.thread_id)

      await user.type(composer, 'and in South Bank?')
      await user.keyboard('{Enter}')
      expect(screen.getByText(/working on your reply/i)).toBeInTheDocument()

      await user.click(screen.getByRole('button', { name: 'New chat' }))
      expect(screen.queryByText('Example Therapy Pty Ltd')).toBeNull()
      expect(
        screen.getByRole('button', { name: CHAT_EXAMPLE_QUESTIONS[0] ?? '' }),
      ).toBeInTheDocument()
      expect(window.localStorage.getItem('ndis-provider-navigator-thread')).toBeNull()

      resolveStale(stale)
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      // The stale reply belongs to the abandoned conversation and stays discarded.
      expect(screen.queryByText(/follow-up understood as/i)).toBeNull()
      expect(screen.queryByText('Example Therapy Pty Ltd')).toBeNull()
      expect(window.localStorage.getItem('ndis-provider-navigator-thread')).toBeNull()

      await user.type(composer, 'What are the rules for short notice cancellations?')
      await user.keyboard('{Enter}')
      expect(await screen.findByText('Example Therapy Pty Ltd')).toBeInTheDocument()
      expect(chat).toHaveBeenLastCalledWith(
        'What are the rules for short notice cancellations?',
        null,
      )
      expect(window.localStorage.getItem('ndis-provider-navigator-thread')).toBe(FRESH_THREAD_ID)
    })

    it('starts a new conversation from the New chat button', async () => {
      window.localStorage.setItem('ndis-provider-navigator-thread', THREAD_ID)
      const getThread = vi.fn<NavigatorApi['getThread']>().mockResolvedValue(THREAD)
      const user = userEvent.setup()
      const chat = vi.fn<NavigatorApi['chat']>().mockResolvedValue(REPLY_1)
      render(<App client={fakeClient({ getThread, chat })} />)
      expect((await screen.findAllByText('Example Therapy Pty Ltd')).length).toBeGreaterThanOrEqual(
        2,
      )

      await user.click(screen.getByRole('button', { name: 'New chat' }))
      expect(window.localStorage.getItem('ndis-provider-navigator-thread')).toBeNull()
      const composer = screen.getByLabelText<HTMLTextAreaElement>(/your message/i)
      expect(composer).toHaveFocus()
      expect(composer.value).toBe('')
      expect(screen.queryByText('Example Therapy Pty Ltd')).toBeNull()

      await user.type(composer, QUESTION)
      await user.keyboard('{Enter}')
      expect(await screen.findByText('Example Therapy Pty Ltd')).toBeInTheDocument()
      expect(chat).toHaveBeenCalledWith(QUESTION, null)
    })
  })
})
