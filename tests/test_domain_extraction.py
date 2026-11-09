"""The extraction rules: evidence gating, total parsing of model output, attribute merging,
budgets and page selection."""

from datetime import UTC, datetime

from ndis.domain.extraction import (
    MAX_EVIDENCE_CHARS,
    MIN_EVIDENCE_CHARS,
    PageAttributes,
    PageClaim,
    ParsedPage,
    ProviderExtraction,
    ServiceClaim,
    StoredPage,
    TokenBudget,
    accepted_claims,
    claim_rejections,
    estimated_tokens,
    merge_attributes,
    page_excerpt,
    page_priority,
    parse_extraction,
    quote_supported,
    select_pages,
)
from ndis.domain.taxonomy import Service

PHYSIO = Service.PHYSIOTHERAPY

# 60 characters of page text with two line breaks, so quotes that span lines are exercised.
PAGE = (
    "Welcome to Gulf Physio.\nWe provide physiotherapy and exercise programs for NDIS "
    "participants\nin Brisbane. Mobile visits available."
)


def quote() -> str:
    return "We provide physiotherapy and exercise programs for NDIS participants"


def test_quote_supported_accepts_verbatim_and_whitespace_variants() -> None:
    assert quote_supported(PAGE, quote())
    # A quote spanning the cleaned text's line breaks still matches.
    assert quote_supported(PAGE, "physiotherapy and exercise programs for NDIS\nparticipants")
    assert quote_supported(PAGE, "   " + quote() + "  ")


def test_quote_supported_rejects_quotes_not_in_the_text() -> None:
    assert not quote_supported(PAGE, "We provide physiotherapy and hydrotherapy programs")
    assert not quote_supported(PAGE, "")
    # Case matters: the model must copy the text, not rewrite it.
    assert not quote_supported(PAGE, quote().replace("physiotherapy", "Physiotherapy"))


def test_quote_supported_rejects_adversarial_quotes() -> None:
    # An invisible character inserted between words breaks the match.
    assert not quote_supported(PAGE, "We provide physio\u200btherapy and exercise programs")
    # A swapped apostrophe cannot pass: the text has none to begin with, and a text with a
    # curly apostrophe rejects its straight twin.
    curly = "It\u2019s mobile physio that comes to your home in Brisbane, seven days a week."
    assert not quote_supported(curly, curly.replace("\u2019", "'"))
    # A quote shorter than the floor, longer than the ceiling, or without letters.
    assert not quote_supported(PAGE, "physiotherapy")
    assert not quote_supported(PAGE, PAGE * 2)
    assert not quote_supported(PAGE, MIN_EVIDENCE_CHARS * "e")
    assert not quote_supported(PAGE, "1234567890 1234567890 1234567890")


def test_evidence_bounds_are_strictly_enforced() -> None:
    assert MIN_EVIDENCE_CHARS == 25
    assert MAX_EVIDENCE_CHARS == 400


def test_parse_extraction_rejects_structural_garbage_without_raising() -> None:
    assert parse_extraction("not json at all") == ParsedPage(rejected=("not_json",))
    assert parse_extraction("[1, 2, 3]") == ParsedPage(rejected=("not_object",))


def test_parse_extraction_accepts_a_valid_answer() -> None:
    payload = (
        '{"claims": [{"service": "physiotherapy", "evidence_quote": "'
        + quote()
        + '", "confidence": 0.9}],'
        ' "age_groups": ["adults", "school_age", "adults"],'
        ' "telehealth": true, "home_visits": false, "languages": [" English ", "Vietnamese"]}'
    )
    parsed = parse_extraction(payload)
    assert parsed.claims == (ServiceClaim(PHYSIO, quote(), 0.9),)
    assert parsed.attributes == PageAttributes(
        age_groups=("adults", "school_age"),
        telehealth=True,
        home_visits=False,
        languages=("English", "Vietnamese"),
    )
    assert parsed.rejected == ()


def test_parse_extraction_rejects_bad_claims_item_by_item() -> None:
    payload = (
        '{"claims": ['
        '"physiotherapy",'
        '{"service": "dog_grooming", "evidence_quote": "we offer dog grooming services", '
        '"confidence": 0.9},'
        '{"service": "physiotherapy", "evidence_quote": 42, "confidence": 0.9},'
        '{"service": "physiotherapy", "evidence_quote": "quote", "confidence": "high"},'
        '{"service": "physiotherapy", "evidence_quote": "quote", "confidence": true},'
        '{"service": "physiotherapy", "evidence_quote": "quote", "confidence": 1.5},'
        '{"service": "physiotherapy", "evidence_quote": "quote", "confidence": -0.1},'
        '{"service": "physiotherapy", "evidence_quote": "quote", "confidence": 0.8}'
        "]}"
    )
    parsed = parse_extraction(payload)
    assert parsed.claims == (ServiceClaim(PHYSIO, "quote", 0.8),)
    # The claim reasons in input order, ahead of the missing-field reasons of other keys.
    assert parsed.rejected[:7] == (
        "claim_not_object",
        "claim_service_unknown",
        "claim_quote_not_string",
        "claim_confidence_not_number",
        "claim_confidence_not_number",
        "claim_confidence_out_of_range",
        "claim_confidence_out_of_range",
    )


def test_parse_extraction_rejects_bad_attributes() -> None:
    payload = (
        '{"claims": [], "age_groups": ["retirees", 7], "telehealth": "yes",'
        ' "home_visits": 1, "languages": ["", 3, "English"]}'
    )
    parsed = parse_extraction(payload)
    assert parsed.attributes.age_groups == ()
    assert parsed.attributes.telehealth is None
    assert parsed.attributes.home_visits is None
    assert parsed.attributes.languages == ("English",)
    assert set(parsed.rejected) == {
        "age_group_unknown",
        "telehealth_not_bool",
        "home_visits_not_bool",
        "language_not_string",
        "language_out_of_range",
    }


def test_parse_extraction_counts_missing_fields() -> None:
    parsed = parse_extraction("{}")
    assert parsed.claims == ()
    assert parsed.attributes == PageAttributes()
    # Only the claims list is required; the attribute fields may be left out entirely.
    assert parsed.rejected == ("claims_missing",)


def test_parse_extraction_caps_runaway_lists() -> None:
    claim = '{"service": "physiotherapy", "evidence_quote": "quote", "confidence": 0.5}'
    parsed = parse_extraction('{"claims": [' + ",".join([claim] * 50) + "]}")
    assert len(parsed.claims) == 40
    assert "claims_too_many" in parsed.rejected
    languages = '["' + '","'.join(f"Language {i}" for i in range(20)) + '"]'
    parsed = parse_extraction('{"languages": ' + languages + "}")
    assert len(parsed.attributes.languages) == 12
    assert "languages_too_many" in parsed.rejected


def test_accepted_claims_gate_and_dedupe() -> None:
    good = ServiceClaim(PHYSIO, quote(), 0.9)
    fabricated = ServiceClaim(Service.NURSING, "We provide registered nursing care 24/7", 0.9)
    duplicate = ServiceClaim(PHYSIO, quote() + "\n", 0.5)
    parsed = ParsedPage(claims=(good, fabricated, duplicate))
    assert accepted_claims(PAGE, parsed) == (good,)
    assert accepted_claims("no such text here", parsed) == ()
    accepted, dropped = claim_rejections(PAGE, parsed)
    assert accepted == (good,)
    assert dropped == ("claim_quote_not_in_page", "claim_duplicate")


def test_merge_attributes_unions_and_resolves_flags() -> None:
    merged = merge_attributes(
        [
            PageAttributes(age_groups=("adults",), telehealth=False, languages=("English",)),
            PageAttributes(age_groups=("adults", "school_age"), telehealth=True),
            PageAttributes(home_visits=True, languages=("English", "Vietnamese")),
        ]
    )
    assert merged == PageAttributes(
        age_groups=("adults", "school_age"),
        telehealth=True,
        home_visits=True,
        languages=("English", "Vietnamese"),
    )
    # False wins over unset, but True beats everything.
    assert merge_attributes([PageAttributes(telehealth=False)]).telehealth is False
    assert merge_attributes([PageAttributes()]).telehealth is None


def test_budget_spends_and_gates() -> None:
    budget = TokenBudget(1000)
    assert not budget.would_exceed(999)
    budget = budget.spend(600)
    assert budget.remaining() == 400
    assert budget.would_exceed(401)
    assert not budget.would_exceed(400)
    budget = budget.spend(600)
    assert budget.remaining() == 0
    assert budget.would_exceed(0) is False
    assert budget.would_exceed(1)


def test_estimated_tokens_is_deterministic_and_coarse() -> None:
    assert estimated_tokens("") == 1
    assert estimated_tokens("x" * 400) == 101


def test_page_priority_puts_home_first() -> None:
    assert page_priority("https://example.com.au/") < page_priority(
        "https://example.com.au/services"
    )
    assert page_priority("https://example.com.au/services") < page_priority(
        "https://example.com.au/services/ndis/physiotherapy"
    )


def test_select_pages_dedupes_caps_and_orders_home_first() -> None:
    deep = StoredPage("k", "https://e.com/a/b/c", None, "deep text")
    home = StoredPage("k", "https://e.com/", None, "home text")
    same_as_home = StoredPage("k", "https://e.com/?x=1", None, "home\n text")
    services = StoredPage("k", "https://e.com/services", None, "services text")
    pages = (deep, same_as_home, services, home)
    assert select_pages(pages, 10) == (home, services, deep)
    assert select_pages(pages, 2) == (home, services)


def test_page_excerpt_cuts_at_line_boundaries() -> None:
    text = "\n".join(f"line {i} with some words to pad the length" for i in range(50))
    excerpt = page_excerpt(text, 60)
    assert len(excerpt) <= 60
    assert excerpt.endswith("pad the length")
    assert page_excerpt(text, 10_000) == text
    assert page_excerpt("", 100) == ""


def test_provider_extraction_service_set() -> None:
    claim = ServiceClaim(PHYSIO, quote(), 0.9)
    extraction = ProviderExtraction(
        provider_key="123",
        claims=(PageClaim("u1", claim), PageClaim("u2", claim)),
        attributes=PageAttributes(),
        extracted_at=datetime.now(UTC),
        input_tokens=10,
        output_tokens=10,
    )
    assert extraction.services() == frozenset({PHYSIO})


def test_served_claims_drop_below_the_confidence_floor_but_the_record_keeps_them() -> None:
    strong = ServiceClaim(PHYSIO, quote(), 0.9)
    weak = ServiceClaim(Service.NURSING, quote(), 0.5)
    extraction = ProviderExtraction(
        provider_key="123",
        claims=(PageClaim("u1", strong), PageClaim("u2", weak)),
        attributes=PageAttributes(),
        extracted_at=datetime.now(UTC),
        input_tokens=10,
        output_tokens=10,
    )
    assert [page_claim.claim for page_claim in extraction.served_claims()] == [strong]
    assert extraction.services() == frozenset({PHYSIO})
    assert len(extraction.claims) == 2  # the recorded cache keeps the weak claim
