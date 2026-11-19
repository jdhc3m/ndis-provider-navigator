import type { AskOut } from '../client'

/**
 * A resolved suburb: the question named "South Bank", the gazetteer resolved it to
 * QLD 4101 in code, and the answer discloses the resolution. Every provider, ABN, URL,
 * quote, distance and price here is fictitious fixture data.
 */
export const LOCALITY: AskOut = {
  question: 'Which approved providers in South Bank, QLD offer speech pathology?',
  refused: false,
  reason: null,
  explanation: null,
  intent: {
    provider_filters: {
      state: 'QLD',
      postcode: null,
      locality: 'South Bank',
      group: null,
      status: 'Approved',
      brisbane_area: null,
    },
    price_query: null,
    rule_question: null,
    decision_request: false,
    parse_rejections: [],
  },
  locality: {
    name: 'South Bank',
    outcome: 'resolved',
    state: 'QLD',
    postcode: '4101',
    candidates: [],
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
      ],
    },
    {
      abn: '22222222222',
      business_name: 'Sample Allied Health Co',
      legal_name: 'The Trustee for Sample Allied Health Trust',
      head_office: { suburb: 'SOUTH BRISBANE', state: 'QLD', postcode: '4101' },
      website: 'https://example.org/sample-allied-health',
      website_domain: 'example.org',
      registration_status: 'Approved',
      registration_expires_on: '2027-09-30',
      registration_groups: ['Therapeutic Supports'],
      brisbane_area: true,
      distance: { km: 0.0, source: 'postcode_centroid' },
      services: [],
    },
  ],
  provider_total: 2,
  distance_origin: '4101',
  price: null,
  price_error: null,
  rule: null,
  caveat: "the register does not record profession; services come from the provider's website",
  steps: [
    {
      kind: 'intent_parse',
      input_summary: 'question (67 chars)',
      output_summary: 'provider_filters',
      ms: 780,
      input_tokens: 590,
      output_tokens: 62,
      cost_usd: 0.000342,
    },
    {
      kind: 'provider_filter',
      input_summary: 'state=QLD locality=South Bank status=Approved',
      output_summary:
        'resolved South Bank to QLD 4101; the postcode covers other suburbs too; 2 of 2 matching providers ranked by distance from 4101: 1 exact, 1 postcode centroid, 0 without distance',
      ms: 44,
      input_tokens: null,
      output_tokens: null,
      cost_usd: null,
    },
    {
      kind: 'evidence',
      input_summary: '2 providers',
      output_summary: '1 verified mentions for 1 providers',
      ms: 9,
      input_tokens: null,
      output_tokens: null,
      cost_usd: null,
    },
    {
      kind: 'guard',
      input_summary: '2 entries, caveat present',
      output_summary: 'valid',
      ms: 1,
      input_tokens: null,
      output_tokens: null,
      cost_usd: null,
    },
  ],
}
