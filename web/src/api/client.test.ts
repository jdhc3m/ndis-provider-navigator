import { describe, expect, it, vi } from 'vitest'

import { ApiError, NETWORK_ERROR_STATUS, createClient } from './client'
import {
  ANSWER,
  META,
  PENDING_LOCALITY,
  REPLY_1,
  REPLY_2,
  REPLY_PENDING,
  THREAD,
  THREAD_NOT_FOUND_DETAIL,
} from './fixtures'

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

  it('POSTs /chat with the thread id only when continuing, and GETs the thread by id', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(REPLY_1))
      .mockResolvedValueOnce(jsonResponse(REPLY_2))
      .mockResolvedValueOnce(jsonResponse(THREAD))
    const client = createClient('http://api.test', fetchImpl)

    await expect(
      client.chat('Which providers in postcode 4101 offer speech pathology?', null),
    ).resolves.toEqual(REPLY_1)
    await expect(client.chat('and in South Bank?', REPLY_1.thread_id)).resolves.toEqual(REPLY_2)
    await expect(client.getThread(REPLY_1.thread_id)).resolves.toEqual(THREAD)

    expect(fetchImpl).toHaveBeenNthCalledWith(1, 'http://api.test/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: 'Which providers in postcode 4101 offer speech pathology?' }),
    })
    expect(fetchImpl).toHaveBeenNthCalledWith(2, 'http://api.test/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: 'and in South Bank?',
        thread_id: REPLY_1.thread_id,
      }),
    })
    expect(fetchImpl).toHaveBeenNthCalledWith(3, `http://api.test/chat/${REPLY_1.thread_id}`, {
      method: 'GET',
      headers: {},
      body: null,
    })
  })

  it('POSTs the resume with the option id, and normalises the needs_input reply', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ ...REPLY_PENDING, pending: { ...PENDING_LOCALITY } }))
    const client = createClient('http://api.test', fetchImpl)

    await expect(client.resumeChat(REPLY_1.thread_id, 'qld-4064')).resolves.toEqual(REPLY_PENDING)

    expect(fetchImpl).toHaveBeenCalledWith(`http://api.test/chat/${REPLY_1.thread_id}/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ option_id: 'qld-4064' }),
    })
  })

  it('exposes the machine reason of a structured chat detail', async () => {
    const client = createClient(
      '',
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(jsonResponse({ detail: { ...THREAD_NOT_FOUND_DETAIL } }, 404)),
    )
    const error = await client.getThread('not-in-memory').catch((value: unknown) => value)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      status: 404,
      reason: 'thread_not_found',
      detail: THREAD_NOT_FOUND_DETAIL.message,
    })
  })
})
