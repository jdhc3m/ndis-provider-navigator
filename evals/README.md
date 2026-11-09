# Evaluation suite

Model-backed tests will live here and use `@pytest.mark.eval`. No model evaluations
exist in ticket 00. CI explicitly reports why they are unavailable rather than
claiming an evaluation passed. Ticket 08 adds the measured thresholds; earlier
tickets add their labelled datasets and evaluation cases.

`er_sample.yaml` is the entity-resolution hand-check sample written by
`scripts/resolve_entities.py` (50 rows, stratified by `match_status` × `strategy`; ambiguous
rows list their register candidates). Fill each
`verdict:` with `correct`, `incorrect` or `unsure`; `test_entity_resolution.py` then
reports precision over the filled labels and skips visibly while none are filled.
