"""Domain rules for pricing-rule chunking, citation grounding and refusal."""

import pytest

from ndis.domain.pricing import (
    STRATEGY_FIXED,
    STRATEGY_HEADING,
    PdfHeading,
    PdfPage,
    PricingChunk,
    RefusalDecision,
    RuleCitation,
    check_citations,
    chunk_corpus,
    collapse_with_map,
    cosine_similarity,
    fixed_chunks,
    heading_chunks,
    locate_heading,
    parse_rule_answer,
    quote_in_text,
    refusal_decision,
)

PAGE = PdfPage(7, "Intro line.\n\nLong paragraph about cancellation rules. " * 12)
HEADINGS = (PdfHeading(7, 2, "Cancellation rules"),)


def test_fixed_chunks_respect_the_token_cap_and_stay_on_one_page() -> None:
    chunks = fixed_chunks(PAGE, max_tokens=100)
    assert chunks
    assert all(chunk.page == 7 and chunk.strategy == STRATEGY_FIXED for chunk in chunks)
    assert all(chunk.tokens() <= 100 for chunk in chunks)
    assert all(chunk.text.strip() for chunk in chunks)


def test_fixed_chunks_are_deterministic_and_lossless() -> None:
    first = fixed_chunks(PAGE, max_tokens=100)
    second = fixed_chunks(PAGE, max_tokens=100)
    assert first == second
    assert " ".join(chunk.text for chunk in first) != ""
    joined = " ".join(chunk.text for chunk in first)
    assert "Long paragraph about cancellation rules." in joined


def test_heading_chunks_never_mix_sections_in_one_chunk() -> None:
    text = (
        "Carried-over text from the previous section that keeps going here.\n"
        "Cancellation rules\n"
        "A provider must not charge a cancellation fee without notice.\n"
        "TTP policy\n"
        "A provider may ask for a payment before delivering supports.\n"
    )
    page = PdfPage(9, text)
    headings = (
        PdfHeading(9, 2, "Cancellation rules"),
        PdfHeading(9, 2, "TTP policy"),
    )
    chunks = heading_chunks(page, headings, max_tokens=600)
    assert len(chunks) == 3
    assert "Carried-over" in chunks[0].text
    assert "cancellation fee" in chunks[1].text and "TTP" not in chunks[1].text
    assert "payment before delivering" in chunks[2].text
    assert all(chunk.strategy == STRATEGY_HEADING and chunk.page == 9 for chunk in chunks)


def test_heading_matching_survives_line_wraps() -> None:
    text = "The NDIS Pricing\nArrangements and\nPrice Limits document applies.\nBody text.\n"
    assert locate_heading(text, "The NDIS Pricing\nArrangements and\nPrice Limits") == 0
    assert locate_heading(text, "Not In This Page") is None


def test_locate_heading_is_case_insensitive() -> None:
    assert locate_heading("Introduction\nBody", "introduction") == 0


def test_collapse_with_map_round_trips_positions() -> None:
    source = "  Hello\n\nWorld "
    collapsed, mapping = collapse_with_map(source)
    assert collapsed == "Hello World"
    assert len(mapping) == len(collapsed)
    # Every non-space collapsed character comes from its mapped original position; the
    # single collapsed space between the words is synthesised, not copied.
    assert (
        "".join(
            source[index] for index, char in zip(mapping, collapsed, strict=True) if char != " "
        )
        == "HelloWorld"
    )
    assert source[mapping[-1]] == "d"


def test_corpus_rejects_unknown_strategy() -> None:
    with pytest.raises(ValueError, match="unknown chunk strategy"):
        chunk_corpus((PAGE,), HEADINGS, "semantic")


def test_corpus_filters_empty_chunks() -> None:
    blank = PdfPage(8, "\n \n\n")
    chunks = chunk_corpus((PAGE, blank), HEADINGS, STRATEGY_FIXED)
    assert all(chunk.page == 7 for chunk in chunks)


def test_cosine_similarity() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
    with pytest.raises(ValueError, match="same dimension"):
        cosine_similarity([1.0], [1.0, 2.0])


def test_parse_rule_answer_happy_path() -> None:
    payload = (
        '{"supported": true, "answer": "Yes, 100 per cent.", '
        '"citations": [{"page": 12, "quote": "cancellation fee of 100 per cent applies"}]}'
    )
    parsed = parse_rule_answer(payload)
    assert parsed.supported is True
    assert parsed.answer == "Yes, 100 per cent."
    assert parsed.citations == (RuleCitation(12, "cancellation fee of 100 per cent applies"),)
    assert parsed.rejected == ()


def test_parse_rule_answer_is_total() -> None:
    assert parse_rule_answer("not json").rejected == ("not_json",)
    assert parse_rule_answer("[1]").rejected == ("not_object",)
    parsed = parse_rule_answer('{"supported": "yes", "citations": "x"}')
    assert parsed.supported is None and "supported_missing" in parsed.rejected
    assert "citations_not_list" in parsed.rejected
    parsed = parse_rule_answer('{"supported": true, "citations": [{}]}')
    assert parsed.citations == ()
    assert "citation_page_not_int" in parsed.rejected
    parsed = parse_rule_answer('{"supported": true, "citations": [{"page": 1}]}')
    assert "citation_quote_not_string" in parsed.rejected
    assert "citations_missing" in parse_rule_answer('{"supported": true}').rejected


def test_quote_in_text_is_verbatim_and_bounded() -> None:
    text = "A cancellation fee of 100 per cent applies when less than 5 business days notice."
    assert quote_in_text(text, "cancellation fee of 100 per cent applies when less")
    assert quote_in_text(text, "CANCELLATION  FEE\nof 100 per cent applies")
    assert not quote_in_text(text, "cancellation fee of 90 per cent applies")
    assert not quote_in_text(text, "cation fee")
    assert not quote_in_text(text, "x" * 401)


def test_check_citations_drops_unknown_pages_and_unsupported_quotes() -> None:
    chunks = (
        PricingChunk(STRATEGY_FIXED, 12, "A cancellation fee of 100 per cent applies."),
        PricingChunk(STRATEGY_FIXED, 13, "Providers may claim non-face-to-face supports."),
    )
    accepted, dropped = check_citations(
        (
            RuleCitation(12, "cancellation fee of 100 per cent applies"),
            RuleCitation(99, "cancellation fee of 100 per cent applies"),
            RuleCitation(13, "cancellation fee of 100 per cent applies"),
            RuleCitation(12, "tiny"),
            RuleCitation(12, ""),
        ),
        chunks,
    )
    assert accepted == (RuleCitation(12, "cancellation fee of 100 per cent applies"),)
    assert dropped == (
        "citation_page_unknown",
        "citation_quote_not_in_page",
        "citation_too_short",
        "citation_empty",
    )


def test_check_citations_drops_duplicates_and_quotes_spanning_chunks_of_one_page() -> None:
    chunks = (
        PricingChunk(STRATEGY_FIXED, 12, "First half of the rule about travel"),
        PricingChunk(STRATEGY_FIXED, 12, "Second half of the rule about travel claims"),
    )
    accepted, dropped = check_citations(
        (
            RuleCitation(12, "First half of the rule about travel"),
            RuleCitation(12, "first half of the rule about travel"),
        ),
        chunks,
    )
    assert len(accepted) == 1
    assert dropped == ("citation_duplicate",)


def test_refusal_decision_order() -> None:
    assert refusal_decision(
        chunks_found=0,
        top_similarity=None,
        threshold=0.3,
        judge_supported=None,
        valid_citations=0,
    ) == RefusalDecision(True, "no_chunks")
    assert refusal_decision(
        chunks_found=3,
        top_similarity=0.2,
        threshold=0.3,
        judge_supported=None,
        valid_citations=0,
    ) == RefusalDecision(True, "below_similarity_threshold")
    assert refusal_decision(
        chunks_found=3,
        top_similarity=0.5,
        threshold=0.3,
        judge_supported=False,
        valid_citations=0,
    ) == RefusalDecision(True, "judge_unsupported")
    assert refusal_decision(
        chunks_found=3,
        top_similarity=0.5,
        threshold=0.3,
        judge_supported=True,
        valid_citations=0,
    ) == RefusalDecision(True, "no_valid_citation")
    assert refusal_decision(
        chunks_found=3,
        top_similarity=0.5,
        threshold=0.3,
        judge_supported=True,
        valid_citations=1,
    ) == RefusalDecision(False, None)
