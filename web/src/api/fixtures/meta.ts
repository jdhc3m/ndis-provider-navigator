import type { MetaOut } from '../client'

/** `/meta` as a fully configured deployment would answer it; model ids are placeholders. */
export const META: MetaOut = {
  llm_model: 'example-generation-model',
  embed_model: 'example-embedding-model',
  llm_configured: true,
  register_snapshot_date: '2026-11-09',
  catalogue_title: 'NDIS Support Catalogue 2026-27',
  financial_year: '2026-27',
  pricing_document: 'NDIS Pricing Arrangements and Price Limits 2025-26 v1.1',
  rule_chunk_strategy: 'heading',
  tracing: { configured: true, url: 'https://traces.example.org' },
}

/** `/meta` on a machine with no model key, no data and no tracing. */
export const META_UNCONFIGURED: MetaOut = {
  llm_model: '',
  embed_model: '',
  llm_configured: false,
  register_snapshot_date: null,
  catalogue_title: 'NDIS Support Catalogue 2026-27',
  financial_year: '2026-27',
  pricing_document: 'NDIS Pricing Arrangements and Price Limits 2025-26 v1.1',
  rule_chunk_strategy: 'heading',
  tracing: { configured: false, url: null },
}
