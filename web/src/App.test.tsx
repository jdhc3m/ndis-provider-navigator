import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { App } from './App'
import { ApiError, NETWORK_ERROR_STATUS, type NavigatorApi } from './api/client'
import {
  AMBIGUOUS,
  ANSWER,
  EMPTY,
  ERROR_DETAIL,
  ERROR_STATUS,
  LOCALITY,
  META,
  REFUSAL,
  SERVICE,
  UNKNOWN_SERVICE,
} from './api/fixtures'
import { EXAMPLE_QUESTIONS } from './examples'

const QUESTION = 'Which approved providers in postcode 4101 offer speech pathology?'

/** A client that never touches the network: every call resolves from the fixtures. */
function fakeClient(overrides: Partial<NavigatorApi> = {}): NavigatorApi {
  return {
    meta: vi.fn(() => Promise.resolve(META)),
    ask: vi.fn(() => Promise.resolve(ANSWER)),
    ...overrides,
  }
}

async function renderAndAsk(client: NavigatorApi, question = QUESTION) {
  const user = userEvent.setup()
  render(<App client={client} />)
  await user.type(screen.getByLabelText(/ask about ndis providers/i), question)
  await user.click(screen.getByRole('button', { name: 'Ask' }))
  return user
}

describe('header', () => {
  it('shows the snapshot date, catalogue, pricing document and model from /meta', async () => {
    render(<App client={fakeClient()} />)
    const header = await screen.findByRole('banner')
    expect(within(header).getByText('9 Nov 2026')).toBeInTheDocument()
    expect(within(header).getByText(META.catalogue_title)).toBeInTheDocument()
    expect(within(header).getByText(META.pricing_document)).toBeInTheDocument()
    expect(within(header).getByText(META.llm_model)).toBeInTheDocument()
  })

  it('keeps the page working when /meta fails', async () => {
    const client = fakeClient({
      meta: () => Promise.reject(new ApiError(503, 'meta unavailable (test)')),
    })
    await renderAndAsk(client)
    expect(
      await screen.findByText(/service details unavailable: meta unavailable \(test\)/i),
    ).toBeInTheDocument()
    expect(await screen.findByText('Example Therapy Pty Ltd')).toBeInTheDocument()
    expect(screen.getByRole('contentinfo')).toBeInTheDocument()
  })
})

describe('answer state', () => {
  it('renders the intent, caveat, providers, service tags, price card and rule card', async () => {
    const client = fakeClient()
    await renderAndAsk(client)

    expect(client.ask).toHaveBeenCalledWith(QUESTION)
    expect(await screen.findByText('Example Therapy Pty Ltd')).toBeInTheDocument()
    expect(screen.getByText('Sample Allied Health Co')).toBeInTheDocument()
    expect(screen.getByText('Placeholder Supports Ltd')).toBeInTheDocument()
    expect(screen.getByText(/showing 3 of 3 matched/i)).toBeInTheDocument()

    // Intent chips
    expect(screen.getByText(/provider_filters: state: QLD, postcode: 4101/)).toBeInTheDocument()
    expect(screen.getByText(/price_query: 15_999_0128_1_3/)).toBeInTheDocument()

    // Caveat line
    expect(screen.getByText(ANSWER.caveat ?? '')).toBeInTheDocument()

    // Registration badges derived from registration_status
    expect(screen.getAllByText('Registered')).toHaveLength(2)
    expect(screen.getByText('Revoked')).toBeInTheDocument()

    // Service tags with closed evidence disclosures
    const speech = screen.getByRole('button', { name: 'Speech pathology evidence' })
    expect(speech).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText(/our speech pathologists work/i)).not.toBeInTheDocument()
    expect(screen.getByText(/no services verified from this provider/i)).toBeInTheDocument()

    // Distances: the origin once above the cards, one exact, one approximate, one missing,
    // in the order the API returned them (nearest first; nothing is sorted client-side).
    expect(screen.getByText(/distances from/i)).toHaveTextContent(
      'Distances from 4101, straight-line, nearest first.',
    )
    const cards = screen.getAllByRole('article', {
      name: /example therapy|sample allied|placeholder supports/i,
    })
    expect(cards.map((card) => within(card).getByRole('heading').textContent)).toEqual([
      'Example Therapy Pty Ltd',
      'Sample Allied Health Co',
      'Placeholder Supports Ltd',
    ])
    const [exact, approximate, unmeasured] = cards as [HTMLElement, HTMLElement, HTMLElement]
    expect(within(exact).getByText('0.4 km')).toBeInTheDocument()
    expect(within(exact).getByText('from Maps listing')).toBeInTheDocument()
    expect(within(approximate).getByText('1.6 km')).toBeInTheDocument()
    expect(within(approximate).getByText('approx. — postcode centroid')).toBeInTheDocument()
    expect(within(unmeasured).getByText('not available')).toBeInTheDocument()
    expect(within(unmeasured).queryByText(/km/)).not.toBeInTheDocument()

    // Price card
    const price = screen.getByRole('article', { name: 'Price limit' })
    expect(within(price).getAllByText('$193.99').length).toBeGreaterThan(0)
    expect(within(price).getByText('15_999_0128_1_3')).toBeInTheDocument()
    expect(within(price).getByText(/from support catalogue/i)).toBeInTheDocument()

    // Rule card with citations
    const rule = screen.getByRole('article', { name: 'Pricing rule' })
    expect(within(rule).getByText(/fixture answer: a provider may claim/i)).toBeInTheDocument()
    expect(within(rule).getByText('page 17')).toBeInTheDocument()
    expect(within(rule).getByText('page 18')).toBeInTheDocument()

    // "How this answer was built" lists one row per step once opened
    expect(
      screen.getByRole('button', { name: /how this answer was built \(6 steps/i }),
    ).toBeVisible()
  })

  it('opens the evidence disclosure with Enter and Space', async () => {
    const user = await renderAndAsk(fakeClient())
    const toggle = await screen.findByRole('button', { name: 'Speech pathology evidence' })

    toggle.focus()
    await user.keyboard('{Enter}')
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText(/our speech pathologists work/i)).toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: /example\.org\/example-therapy\/services/ }),
    ).toHaveAttribute('href', 'https://example.org/example-therapy/services')

    await user.keyboard(' ')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText(/our speech pathologists work/i)).not.toBeInTheDocument()
  })

  it('lists every step with its timing when the disclosure is opened', async () => {
    const user = await renderAndAsk(fakeClient())
    await user.click(await screen.findByRole('button', { name: /how this answer was built/i }))
    const table = screen.getByRole('table', { name: /pipeline steps/i })
    for (const step of ANSWER.steps) {
      expect(within(table).getByText(step.kind)).toBeInTheDocument()
    }
    expect(within(table).getByText('842 ms')).toBeInTheDocument()
    expect(within(table).getByText('1.93 s')).toBeInTheDocument()
  })

  it('shows the trace link only when /meta reports tracing as configured', async () => {
    const user = await renderAndAsk(fakeClient())
    await user.click(await screen.findByRole('button', { name: /how this answer was built/i }))
    expect(screen.getByRole('link', { name: /open the trace viewer/i })).toHaveAttribute(
      'href',
      META.tracing.url,
    )
  })
})

describe('locality resolution', () => {
  it('shows the resolved suburb as a chip with the covers-other-suburbs note', async () => {
    const client = fakeClient({ ask: () => Promise.resolve(LOCALITY) })
    await renderAndAsk(client, LOCALITY.question)

    const row = await screen.findByRole('region', { name: 'Understood as' })
    expect(within(row).getByText(/locality: South Bank → QLD 4101/)).toBeInTheDocument()
    expect(within(row).getByText(/the postcode covers other suburbs too/)).toBeInTheDocument()
    expect(
      within(row).getByText(/provider_filters: state: QLD, locality: South Bank/),
    ).toBeInTheDocument()
    // The resolution is disclosed over the cards too.
    expect(await screen.findByText(/distances from/i)).toHaveTextContent(
      'Distances from 4101, straight-line, nearest first.',
    )
  })

  it('shows an ambiguous suburb as buttons that re-ask with the chosen postcode', async () => {
    const ask = vi.fn<NavigatorApi['ask']>().mockResolvedValue(AMBIGUOUS)
    const user = await renderAndAsk(fakeClient({ ask }), AMBIGUOUS.question)

    const row = await screen.findByRole('region', { name: 'Understood as' })
    expect(within(row).getByText('locality: West End matched 3 postcodes')).toBeInTheDocument()
    expect(within(row).getByText('rejected: locality_ambiguous')).toBeInTheDocument()

    const buttons = within(row).getAllByRole('button', { name: /ask again with/i })
    expect(buttons.map((button) => button.textContent)).toEqual([
      'Ask again with QLD 4101',
      'Ask again with QLD 4810',
      'Ask again with WA 6530',
    ])

    const chosen = buttons[0]
    if (!chosen) throw new Error('candidate buttons missing')
    await user.click(chosen)
    expect(ask).toHaveBeenLastCalledWith(`${AMBIGUOUS.question} (postcode 4101)`)
    // The query box carries the chosen postcode for the next edit.
    expect(screen.getByLabelText<HTMLInputElement>(/ask about ndis providers/i).value).toBe(
      `${AMBIGUOUS.question} (postcode 4101)`,
    )
  })
})

describe('service price list', () => {
  it('renders the catalogue list with the chip, the phrase and the match count', async () => {
    const client = fakeClient({ ask: () => Promise.resolve(SERVICE) })
    await renderAndAsk(client, SERVICE.question)

    // The "Understood as" row carries the phrase and the match count.
    const row = await screen.findByRole('region', { name: 'Understood as' })
    expect(
      within(row).getByText('price_query: service "occupational therapy" · QLD · matched 6'),
    ).toBeInTheDocument()

    // The price column is the list, not a single price card.
    const card = await screen.findByRole('article', { name: 'Price limits' })
    expect(within(card).getByText(/from support catalogue — not generated/i)).toBeInTheDocument()
    expect(within(card).getByText(/showing 6 of 6 support items matching/i)).toBeInTheDocument()
    expect(within(card).getAllByText('$193.99').length).toBeGreaterThanOrEqual(3)
    expect(within(card).getByText('$97.00')).toBeInTheDocument()
    expect(within(card).getByText(/01_999_0128_1_3/)).toBeInTheDocument()
    expect(within(card).getAllByText(/per hour/).length).toBeGreaterThanOrEqual(5)
    expect(within(card).queryByText(/per month/)).not.toBeInTheDocument()
    expect(within(card).getByText('Quotable item')).toBeInTheDocument()
    expect(within(card).queryByText(/legacy item/)).not.toBeInTheDocument()
    // No single-item price card renders next to the list.
    expect(screen.queryByText('15_999_0128_1_3')).not.toBeInTheDocument()
  })

  it('says the national limit applies when the question stated no state', async () => {
    const national = {
      ...SERVICE,
      intent: SERVICE.intent
        ? {
            ...SERVICE.intent,
            price_query: SERVICE.intent.price_query
              ? { ...SERVICE.intent.price_query, state: null }
              : null,
          }
        : null,
      service_prices: SERVICE.service_prices ? { ...SERVICE.service_prices, state: null } : null,
    } satisfies typeof SERVICE
    const client = fakeClient({ ask: () => Promise.resolve(national) })
    await renderAndAsk(client, 'What is the price limit for occupational therapy?')

    const card = await screen.findByRole('article', { name: 'Price limits' })
    expect(
      within(card).getByText(/no state stated, so the national limit applies/i),
    ).toBeInTheDocument()
    const row = screen.getByRole('region', { name: 'Understood as' })
    expect(within(row).getByText(/national \(no state stated\)/)).toBeInTheDocument()
  })

  it('shows the unknown-service state without inventing a price', async () => {
    const client = fakeClient({ ask: () => Promise.resolve(UNKNOWN_SERVICE) })
    await renderAndAsk(client, UNKNOWN_SERVICE.question)

    const status = await screen.findByRole('status')
    expect(within(status).getByText(/no catalogue match/i)).toBeInTheDocument()
    expect(within(status).getByText(/underwater basket weaving/i)).toBeInTheDocument()
    expect(screen.queryByRole('article', { name: 'Price limits' })).not.toBeInTheDocument()
    expect(screen.queryByRole('article', { name: 'Price limit' })).not.toBeInTheDocument()
    // The intent chip still shows the phrase, with zero matches.
    const row = screen.getByRole('region', { name: 'Understood as' })
    expect(
      within(row).getByText(
        'price_query: service "underwater basket weaving" · national (no state stated) · matched 0',
      ),
    ).toBeInTheDocument()
  })

  it('offers no example chip that contains an item number', () => {
    for (const question of EXAMPLE_QUESTIONS) {
      expect(question).not.toMatch(/\d{2}_\d{3}/)
    }
  })
})

describe('refusal state', () => {
  it('replaces both columns with the banner and shows the red decision_request chip', async () => {
    const client = fakeClient({ ask: () => Promise.resolve(REFUSAL) })
    await renderAndAsk(client, 'Which provider is best for my child?')

    const banner = await screen.findByRole('status', { name: 'Not answered' })
    expect(within(banner).getByText(/decision_request/)).toBeInTheDocument()
    expect(within(banner).getByText(REFUSAL.explanation ?? '')).toBeInTheDocument()

    expect(screen.getByText('decision_request', { selector: 'li *' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Providers' })).not.toBeInTheDocument()
    expect(screen.queryByRole('article', { name: 'Price limit' })).not.toBeInTheDocument()
    expect(screen.queryByRole('article', { name: 'Pricing rule' })).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /how this answer was built \(1 step,/i }),
    ).toBeVisible()
  })
})

describe('empty state', () => {
  it('shows 0 matched, echoes the filters and offers the example chips', async () => {
    const client = fakeClient({ ask: () => Promise.resolve(EMPTY) })
    await renderAndAsk(client, 'Which approved providers in postcode 4999 offer speech pathology?')

    expect(await screen.findByText('0 matched')).toBeInTheDocument()
    expect(screen.getByText(/showing 0 of 0 matched/i)).toBeInTheDocument()
    expect(screen.getByText('postcode: 4999', { selector: 'span *' })).toBeInTheDocument()
    expect(screen.getByText(/broaden your search/i)).toBeInTheDocument()
    expect(
      screen.getByText(/did not ask about a price limit or a pricing rule/i),
    ).toBeInTheDocument()
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
    // The example chips appear twice: under the query box and inside the empty state.
    expect(screen.getAllByRole('button', { name: EXAMPLE_QUESTIONS[1] ?? '' })).toHaveLength(2)
  })
})

describe('error state', () => {
  it('shows the server detail and retries on request', async () => {
    const ask = vi
      .fn<NavigatorApi['ask']>()
      .mockRejectedValueOnce(new ApiError(ERROR_STATUS, ERROR_DETAIL))
      .mockResolvedValueOnce(ANSWER)
    const user = await renderAndAsk(fakeClient({ ask }))

    const alert = await screen.findByRole('alert')
    expect(
      within(alert).getByText(`The service answered with status ${ERROR_STATUS}`),
    ).toBeVisible()
    expect(within(alert).getByText(ERROR_DETAIL)).toBeInTheDocument()

    await user.click(within(alert).getByRole('button', { name: /try again/i }))
    expect(await screen.findByText('Example Therapy Pty Ltd')).toBeInTheDocument()
    expect(ask).toHaveBeenCalledTimes(2)
    expect(ask).toHaveBeenLastCalledWith(QUESTION)
  })

  it('names a network failure distinctly', async () => {
    const client = fakeClient({
      ask: () => Promise.reject(new ApiError(NETWORK_ERROR_STATUS, 'Failed to fetch')),
    })
    await renderAndAsk(client)
    const alert = await screen.findByRole('alert')
    expect(within(alert).getByText(/could not reach the service/i)).toBeInTheDocument()
  })
})

describe('query form', () => {
  it('fills the box from an example chip and disables Ask for short questions', async () => {
    const user = userEvent.setup()
    render(<App client={fakeClient()} />)
    const box = screen.getByLabelText<HTMLTextAreaElement>(/ask about ndis providers/i)
    const ask = screen.getByRole('button', { name: 'Ask' })
    expect(ask).toBeDisabled()

    await user.type(box, 'hi')
    expect(ask).toBeDisabled()

    await user.click(screen.getByRole('button', { name: EXAMPLE_QUESTIONS[2] ?? '' }))
    expect(box.value).toBe(EXAMPLE_QUESTIONS[2])
    expect(ask).toBeEnabled()
  })

  it('asks the ?q= question on load and shows a busy loading state meanwhile', async () => {
    let resolveAsk: (value: typeof ANSWER) => void = () => undefined
    const ask = vi.fn<NavigatorApi['ask']>(
      () =>
        new Promise((resolve) => {
          resolveAsk = resolve
        }),
    )
    render(<App client={fakeClient({ ask })} initialQuestion={QUESTION} />)
    expect(ask).toHaveBeenCalledWith(QUESTION)
    expect(screen.getByRole('status', { busy: true })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /asking/i })).toBeDisabled()

    resolveAsk(ANSWER)
    expect(await screen.findByText('Example Therapy Pty Ltd')).toBeInTheDocument()
    expect(screen.queryByRole('status', { busy: true })).not.toBeInTheDocument()
  })
})
