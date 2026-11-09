import { describe, expect, it, vi } from 'vitest'

import { ApiError, NETWORK_ERROR_STATUS, createClient } from './client'
import { ANSWER, META } from './fixtures'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('createClient', () => {
  it('GETs /meta and POSTs /ask as JSON against the base URL', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(META))
      .mockResolvedValueOnce(jsonResponse(ANSWER))
    const client = createClient('http://api.test', fetchImpl)

    await expect(client.meta()).resolves.toEqual(META)
    await expect(client.ask('q?')).resolves.toEqual(ANSWER)

    expect(fetchImpl).toHaveBeenNthCalledWith(1, 'http://api.test/meta', {
      method: 'GET',
      headers: {},
      body: null,
    })
    expect(fetchImpl).toHaveBeenNthCalledWith(2, 'http://api.test/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: 'q?' }),
    })
  })

  it("surfaces FastAPI's detail string on a non-2xx response", async () => {
    const client = createClient(
      '',
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ detail: 'intent parse failed' }, 502)),
    )
    const error = await client.ask('q?').catch((value: unknown) => value)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 502, detail: 'intent parse failed' })
  })

  it('flattens validation errors into one detail string', async () => {
    const body = {
      detail: [{ loc: ['body', 'question'], msg: 'String should have at least 3 characters' }],
    }
    const client = createClient(
      '',
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(body, 422)),
    )
    await expect(client.ask('hi')).rejects.toMatchObject({
      status: 422,
      detail: 'body.question: String should have at least 3 characters',
    })
  })

  it('maps a thrown fetch to status 0', async () => {
    const client = createClient(
      '',
      vi.fn<typeof fetch>().mockRejectedValue(new TypeError('Failed to fetch')),
    )
    await expect(client.meta()).rejects.toMatchObject({
      status: NETWORK_ERROR_STATUS,
      detail: 'Failed to fetch',
    })
  })
})
