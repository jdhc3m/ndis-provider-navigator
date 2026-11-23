/** The four example questions offered as chips under the search page's query box. */
export const EXAMPLE_QUESTIONS: readonly string[] = [
  'Which approved providers in South Bank, QLD offer speech pathology?',
  'What is the price limit for occupational therapy in Queensland?',
  'Can a provider charge for a short-notice cancellation?',
  'List registered Plan Management providers in the Brisbane area.',
]

/** The four example first messages offered as chips in the chat composer; follow-ups
 * then narrow or extend them in the conversation's own words. Kept word-distinct from
 * the search page's examples so a chip names exactly one tab's list. */
export const CHAT_EXAMPLE_QUESTIONS: readonly string[] = [
  'Which providers in postcode 4101 offer speech pathology?',
  'How much does support coordination cost per hour?',
  'What are the rules for short notice cancellations?',
  'Show me approved providers in the Brisbane area.',
]
