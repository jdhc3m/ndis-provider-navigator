import type { AskOut } from '../client'

/**
 * An ambiguous suburb: "West End" matches three postcodes (QLD 4101, QLD 4810, WA 6530),
 * so no provider query ran; the candidates are disclosed as buttons that ask again with
 * the chosen postcode. Fictitious fixture data throughout.
 */
export const AMBIGUOUS: AskOut = {
  question: 'Show me providers in West End.',
  refused: false,
  reason: null,
  explanation: null,
  intent: {
    provider_filters: {
      state: null,
      postcode: null,
      locality: 'West End',
      group: null,
      status: null,
      brisbane_area: null,
    },
    price_query: null,
    rule_question: null,
    decision_request: false,
    parse_rejections: ['locality_ambiguous'],
  },
  locality: {
    name: 'West End',
    outcome: 'locality_ambiguous',
    state: null,
    postcode: null,
    candidates: [
      { name: 'WEST END', state: 'QLD', postcode: '4101' },
      { name: 'WEST END', state: 'QLD', postcode: '4810' },
      { name: 'WEST END', state: 'WA', postcode: '6530' },
    ],
  },
  providers: [],
  provider_total: 0,
  distance_origin: null,
  price: null,
  price_error: null,
  rule: null,
  caveat: null,
  steps: [
    {
      kind: 'intent_parse',
      input_summary: 'question (30 chars)',
      output_summary: 'provider_filters (rejected: locality_ambiguous)',
      ms: 640,
      input_tokens: 512,
      output_tokens: 48,
      cost_usd: 0.00028,
    },
    {
      kind: 'provider_filter',
      input_summary: 'locality=West End',
      output_summary:
        'no provider query: locality_ambiguous (candidates: QLD 4101, QLD 4810, WA 6530)',
      ms: 2,
      input_tokens: null,
      output_tokens: null,
      cost_usd: null,
    },
    {
      kind: 'evidence',
      input_summary: '0 providers',
      output_summary: '0 verified mentions for 0 providers',
      ms: 0,
      input_tokens: null,
      output_tokens: null,
      cost_usd: null,
    },
    {
      kind: 'guard',
      input_summary: '0 entries, caveat absent',
      output_summary: 'valid',
      ms: 0,
      input_tokens: null,
      output_tokens: null,
      cost_usd: null,
    },
  ],
}
