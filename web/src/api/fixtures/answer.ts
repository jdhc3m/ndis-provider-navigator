import type { AskOut } from '../client'

/**
 * A full answer: providers ranked by distance from 4101 (one exact, one approximate, one
 * unmeasured) with verified services, a price limit and a rule answer. Every provider, ABN,
 * URL, quote, distance and price here is fictitious fixture data.
 */
export const ANSWER: AskOut = {
  question:
    'Which approved providers in postcode 4101 offer speech pathology, what is the price limit for 15_999_0128_1_3 in Queensland, and can a provider charge for a short-notice cancellation?',
  refused: false,
  reason: null,
  explanation: null,
  intent: {
    provider_filters: {
      state: 'QLD',
      postcode: '4101',
      group: null,
      status: 'Approved',
      brisbane_area: null,
    },
    price_query: { item: '15_999_0128_1_3', state: 'QLD', remoteness: null },
    rule_question: 'Can a provider charge for a short-notice cancellation?',
    decision_request: false,
    parse_rejections: [],
  },
  providers: [
    {
      abn: '11111111111',
      business_name: 'Example Therapy Pty Ltd',
      legal_name: 'Example Therapy Pty Ltd',
      head_office: { suburb: 'WEST END', state: 'QLD', postcode: '4101' },
      website: 'https://example.org/example-therapy',
      website_domain: 'example.org',
      registration_status: 'Approved',
      registration_expires_on: '2028-03-31',
      registration_groups: ['Therapeutic Supports', 'Early Childhood Supports'],
      brisbane_area: true,
      distance: { km: 0.4, source: 'exact' },
      services: [
        {
          service: 'Speech pathology',
          url: 'https://example.org/example-therapy/services',
          quote:
            'Our speech pathologists work with children and adults on speech, language, literacy and swallowing.',
          fetched_at: '2026-11-07',
        },
        {
          service: 'Occupational therapy',
          url: 'https://example.org/example-therapy/services',
          quote:
            'Occupational therapy for daily living skills, sensory needs and assistive technology.',
          fetched_at: '2026-11-07',
        },
      ],
    },
    {
      abn: '22222222222',
      business_name: 'Sample Allied Health Co',
      legal_name: 'The Trustee for Sample Allied Health Trust',
      head_office: { suburb: 'WOOLLOONGABBA', state: 'QLD', postcode: '4102' },
      website: 'https://example.org/sample-allied-health',
      website_domain: 'example.org',
      registration_status: 'Revoked',
      registration_expires_on: '2026-01-15',
      registration_groups: ['Therapeutic Supports'],
      brisbane_area: true,
      distance: { km: 1.6, source: 'postcode_centroid' },
      services: [],
    },
    {
      abn: '33333333333',
      business_name: 'Placeholder Supports Ltd',
      legal_name: 'Placeholder Supports Ltd',
      head_office: { suburb: 'HIGHGATE HILL', state: 'QLD', postcode: null },
      website: null,
      website_domain: null,
      registration_status: 'Approved',
      registration_expires_on: '2027-09-30',
      registration_groups: ['Assistance with Daily Life', 'Support Coordination'],
      brisbane_area: true,
      distance: null,
      services: [
        {
          service: 'Support coordination',
          url: 'https://example.org/placeholder-supports/what-we-do',
          quote: 'Our support coordinators help you understand and use your NDIS plan.',
          fetched_at: '2026-11-06',
        },
      ],
    },
  ],
  provider_total: 3,
  distance_origin: '4101',
  price: {
    item_number: '15_999_0128_1_3',
    name: 'Example assessment and therapy item (fixture data)',
    registration_group_number: '0128',
    registration_group_name: 'Therapeutic Supports',
    unit: 'H',
    quotable: false,
    legacy: false,
    start_date: '2026-07-01',
    end_date: null,
    state: 'QLD',
    remoteness: 'national',
    price_limit: '193.99',
    limits: { national: '193.99', remote: '271.59', very_remote: '290.99' },
    source: 'NDIS Support Catalogue 2026-27',
  },
  price_error: null,
  rule: {
    question: 'Can a provider charge for a short-notice cancellation?',
    answer:
      'Fixture answer: a provider may claim for a short-notice cancellation at the agreed price when the participant cancels inside the notice period and the provider could not find other billable work for the worker.',
    citations: [
      {
        page: 17,
        quote:
          'Fixture quote: a provider may claim 100% of the agreed price for a short notice cancellation.',
      },
      {
        page: 18,
        quote:
          'Fixture quote: providers should seek to find alternative billable work for the relevant worker.',
      },
    ],
    refused: false,
    reason: null,
    top_similarity: 0.81,
  },
  caveat: "the register does not record profession; services come from the provider's website",
  steps: [
    {
      kind: 'intent_parse',
      input_summary: 'question (178 chars)',
      output_summary: 'provider_filters, price_query, rule_question',
      ms: 842,
      input_tokens: 612,
      output_tokens: 94,
      cost_usd: 0.000419,
    },
    {
      kind: 'provider_filter',
      input_summary: 'state=QLD postcode=4101 status=Approved',
      output_summary:
        '3 of 3 matching providers ranked by distance from 4101: 1 exact, 1 postcode centroid, 1 without distance',
      ms: 41,
      input_tokens: null,
      output_tokens: null,
      cost_usd: null,
    },
    {
      kind: 'price_limit',
      input_summary: 'item=15_999_0128_1_3 state=QLD remoteness=national',
      output_summary: 'limit 193.99',
      ms: 3,
      input_tokens: null,
      output_tokens: null,
      cost_usd: null,
    },
    {
      kind: 'rule_answer',
      input_summary: 'rule question (53 chars)',
      output_summary: 'answered with 2 citations, top similarity 0.81',
      ms: 1930,
      input_tokens: null,
      output_tokens: null,
      cost_usd: null,
    },
    {
      kind: 'evidence',
      input_summary: '3 providers',
      output_summary: '3 verified mentions for 2 providers',
      ms: 12,
      input_tokens: null,
      output_tokens: null,
      cost_usd: null,
    },
    {
      kind: 'guard',
      input_summary: '3 entries, caveat present',
      output_summary: 'valid',
      ms: 1,
      input_tokens: null,
      output_tokens: null,
      cost_usd: null,
    },
  ],
}
