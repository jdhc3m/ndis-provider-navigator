import type { AskOut } from '../client'

/** A decision request: the intent parsed, the guard refused, nothing was executed. */
export const REFUSAL: AskOut = {
  question: 'Which provider is best for my child?',
  refused: true,
  reason: 'decision_request',
  explanation:
    'This system does not make recommendations and does not make funding decisions. It can list registered providers that match your filters, show what their websites say they offer, and quote the published price limits and pricing rules.',
  intent: {
    provider_filters: null,
    price_query: null,
    rule_question: null,
    decision_request: true,
    parse_rejections: [],
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
      input_summary: 'question (36 chars)',
      output_summary: 'decision_request',
      ms: 655,
      input_tokens: 540,
      output_tokens: 31,
      cost_usd: 0.00024,
    },
  ],
}
