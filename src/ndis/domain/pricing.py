"""Pure rules for turning the Pricing Arrangements PDF into retrievable chunks and for
grounding rule answers in them.

Nothing here touches pymupdf, embeddings or the LLM: the adapter hands over per-page text
and the document's heading titles, and this module decides how that text is cut into
page-tagged chunks. Two strategies exist because the retrieval report compares them; the
strategy is a parameter, not a fork. Citations a model proposes are only trusted when the
quoted span is a verbatim (whitespace-collapsed) piece of the chunk text it is cited from
— the same rule ticket 05 applies to evidence quotes, and for the same reason: the prompt
is a suggestion, this check is code.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from ndis.domain.extraction import estimated_tokens

# The one document the rule index is built from (see scripts/fetch_public_data.py for the
# download); the API and the retrieval report both name it from here.
PRICING_DOCUMENT_TITLE = "NDIS Pricing Arrangements and Price Limits 2025-26 v1.1"

STRATEGY_FIXED = "fixed"
STRATEGY_HEADING = "heading"
CHUNK_STRATEGIES: tuple[str, ...] = (STRATEGY_FIXED, STRATEGY_HEADING)

# A chunk near 600 estimated tokens (~2400 characters) is big enough to carry context and
# small enough that one retrieved chunk is about one rule topic.
DEFAULT_CHUNK_TOKENS = 600

# A citation shorter than this (after collapsing) can match a bare heading fragment such
# as "Price Limits"; longer than this is a copied page, not a quotation.
MIN_CITATION_CHARS = 15
MAX_CITATION_CHARS = 400

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(])")


@dataclass(frozen=True)
class PdfPage:
    """The cleaned text of one PDF page; ``page`` is the 1-based printed page number."""

    page: int
    text: str


@dataclass(frozen=True)
class PdfHeading:
    """One table-of-contents entry: the heading title and the 1-based page it starts on."""

    page: int
    level: int
    title: str


@dataclass(frozen=True)
class PricingChunk:
    """One retrievable piece of the pricing document, tagged with its source page."""

    strategy: str
    page: int
    text: str

    def tokens(self) -> int:
        return estimated_tokens(self.text)


def collapse_with_map(text: str) -> tuple[str, list[int]]:
    """Whitespace-collapsed ``text`` plus, for each collapsed character, its original index.

    ``find`` on the collapsed string can therefore be mapped back to a position in the
    original text, so a heading whose title is wrapped over two lines is still located.
    """
    collapsed: list[str] = []
    mapping: list[int] = []
    pending_space = False
    for index, character in enumerate(text):
        if character.isspace():
            if collapsed:
                pending_space = True
            continue
        if pending_space:
            collapsed.append(" ")
            mapping.append(index)
            pending_space = False
        collapsed.append(character)
        mapping.append(index)
    return "".join(collapsed), mapping


def locate_heading(text: str, title: str) -> int | None:
    """The character index in ``text`` where ``title`` starts, or ``None``.

    Both sides are whitespace-collapsed and case-folded, so a title the PDF wraps across
    two lines still matches. The earliest occurrence wins.
    """
    collapsed, mapping = collapse_with_map(text)
    needle = " ".join(title.split()).casefold()
    if not needle:
        return None
    found = collapsed.casefold().find(needle)
    if found < 0:
        return None
    return mapping[found]


def _split_oversized(block: str, max_tokens: int) -> list[str]:
    """``block`` cut into pieces each within ``max_tokens`` estimated tokens.

    Sentences are kept whole where possible; a single sentence longer than the cap is cut
    on a word boundary so no piece ever exceeds the cap.
    """
    pieces: list[str] = []
    current: list[str] = []
    current_tokens = 1
    for sentence in _SENTENCE_BOUNDARY.split(block.strip()):
        tokens = estimated_tokens(sentence)
        if tokens > max_tokens:
            if current:
                pieces.append(" ".join(current))
                current, current_tokens = [], 1
            words = sentence.split()
            piece: list[str] = []
            piece_tokens = 1
            for word in words:
                word_tokens = estimated_tokens(word)
                if piece and piece_tokens + word_tokens > max_tokens:
                    pieces.append(" ".join(piece))
                    piece, piece_tokens = [], 1
                piece.append(word)
                piece_tokens += word_tokens
            if piece:
                pieces.append(" ".join(piece))
            continue
        if current and current_tokens + tokens > max_tokens:
            pieces.append(" ".join(current))
            current, current_tokens = [], 1
        current.append(sentence)
        current_tokens += tokens
    if current:
        pieces.append(" ".join(current))
    return pieces


def _pack_blocks(blocks: Sequence[str], max_tokens: int) -> list[str]:
    """Whole blocks joined with newlines while the estimated token count stays within the
    cap; an oversized block is sentence-split by ``_split_oversized`` first."""
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 1
    for block in blocks:
        stripped = block.strip()
        if not stripped:
            continue
        tokens = estimated_tokens(stripped)
        if tokens > max_tokens:
            if current:
                chunks.append("\n".join(current))
                current, current_tokens = [], 1
            chunks.extend(_split_oversized(stripped, max_tokens))
            continue
        if current and current_tokens + tokens > max_tokens:
            chunks.append("\n".join(current))
            current, current_tokens = [], 1
        current.append(stripped)
        current_tokens += tokens
    if current:
        chunks.append("\n".join(current))
    return chunks


def _page_blocks(text: str) -> list[str]:
    """Paragraph blocks of one page: line groups separated by blank lines, then lines
    joined with spaces inside a block."""
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            if current:
                blocks.append(" ".join(current))
                current = []
            continue
        current.append(line.strip())
    if current:
        blocks.append(" ".join(current))
    return blocks


def fixed_chunks(page: PdfPage, max_tokens: int = DEFAULT_CHUNK_TOKENS) -> tuple[PricingChunk, ...]:
    """One page cut into fixed-size token windows at paragraph (then sentence) boundaries.

    Chunks never span pages, so every citation resolves to exactly one printed page."""
    return tuple(
        PricingChunk(STRATEGY_FIXED, page.page, chunk)
        for chunk in _pack_blocks(_page_blocks(page.text), max_tokens)
    )


def heading_chunks(
    page: PdfPage, headings: Sequence[PdfHeading], max_tokens: int = DEFAULT_CHUNK_TOKENS
) -> tuple[PricingChunk, ...]:
    """One page cut at its section boundaries first, then packed into token windows.

    Text before the first located heading (a section continued from the previous page) is
    its own chunk, so a heading never shares a chunk with the previous section's text and
    the strategy keeps rule sections whole whenever they fit the cap."""
    titles = [heading.title for heading in headings if heading.page == page.page]
    offsets = sorted(
        offset
        for offset in (locate_heading(page.text, title) for title in titles)
        if offset is not None
    )
    sections: list[str] = []
    start = 0
    for offset in offsets:
        if offset > start:
            sections.append(page.text[start:offset])
        start = offset
    sections.append(page.text[start:])
    chunks: list[PricingChunk] = []
    for section in sections:
        for piece in _pack_blocks(_page_blocks(section), max_tokens):
            chunks.append(PricingChunk(STRATEGY_HEADING, page.page, piece))
    return tuple(chunks)


def chunk_corpus(
    pages: Sequence[PdfPage],
    headings: Sequence[PdfHeading],
    strategy: str,
    max_tokens: int = DEFAULT_CHUNK_TOKENS,
) -> tuple[PricingChunk, ...]:
    """The whole document chunked with one strategy. ``strategy`` must be one of
    ``CHUNK_STRATEGIES``; anything else is a programming error, not a runtime event."""
    if strategy not in CHUNK_STRATEGIES:
        raise ValueError(f"unknown chunk strategy: {strategy}")
    chunks: list[PricingChunk] = []
    for page in pages:
        if strategy == STRATEGY_FIXED:
            chunks.extend(fixed_chunks(page, max_tokens))
        else:
            chunks.extend(heading_chunks(page, headings, max_tokens))
    return tuple(chunk for chunk in chunks if chunk.text.strip())


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine of the angle between two vectors; 0.0 when either is all zeros."""
    if len(a) != len(b):
        raise ValueError("vectors must have the same dimension")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(dot / (norm_a * norm_b))


@dataclass(frozen=True)
class RuleCitation:
    """A citation the model proposed: the printed page and the span it quotes there."""

    page: int
    quote: str


@dataclass(frozen=True)
class ParsedRuleAnswer:
    """What survived parsing one judge/answer payload; ``rejected`` holds machine reasons."""

    supported: bool | None
    answer: str | None
    citations: tuple[RuleCitation, ...]
    rejected: tuple[str, ...]


def parse_rule_answer(payload: str) -> ParsedRuleAnswer:
    """Parse the model's judge JSON. Total: any structural surprise becomes a rejection
    reason, never an exception, so a bad answer can only lose its own data."""
    try:
        document = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return ParsedRuleAnswer(None, None, (), ("not_json",))
    if not isinstance(document, dict):
        return ParsedRuleAnswer(None, None, (), ("not_object",))
    rejected: list[str] = []
    supported = document.get("supported")
    if not isinstance(supported, bool):
        rejected.append("supported_missing")
        supported = None
    answer = document.get("answer")
    if answer is not None and not isinstance(answer, str):
        rejected.append("answer_not_string")
        answer = None
    citations: list[RuleCitation] = []
    raw = document.get("citations")
    if raw is None:
        rejected.append("citations_missing")
    elif not isinstance(raw, list):
        rejected.append("citations_not_list")
    else:
        for item in raw:
            if not isinstance(item, dict):
                rejected.append("citation_not_object")
                continue
            page = item.get("page")
            quote = item.get("quote")
            if isinstance(page, bool) or not isinstance(page, int):
                rejected.append("citation_page_not_int")
                continue
            if not isinstance(quote, str):
                rejected.append("citation_quote_not_string")
                continue
            citations.append(RuleCitation(page, quote))
    return ParsedRuleAnswer(supported, answer, tuple(citations), tuple(rejected))


def quote_in_text(text: str, quote: str) -> bool:
    """True only when ``quote`` is a genuine span of ``text``.

    Both sides are whitespace-collapsed and compared case-insensitively, so a quote that
    spans a line break or differs in letter case still matches, but every other character
    must match exactly: an inserted zero-width space, a swapped apostrophe or a reworded
    sentence all fail. A quote shorter than ``MIN_CITATION_CHARS`` could name a bare
    heading fragment, longer than ``MAX_CITATION_CHARS`` is a copied page rather than a
    quotation, and a quote with no alphanumeric character at all is noise; none of those
    are evidence.
    """
    collapsed_quote = " ".join(quote.split())
    if not MIN_CITATION_CHARS <= len(collapsed_quote) <= MAX_CITATION_CHARS:
        return False
    if not any(character.isalnum() for character in collapsed_quote):
        return False
    return " ".join(text.split()).casefold().find(collapsed_quote.casefold()) >= 0


def check_citations(
    citations: Sequence[RuleCitation], chunks: Sequence[PricingChunk]
) -> tuple[tuple[RuleCitation, ...], tuple[str, ...]]:
    """The citations whose quotes actually appear in a chunk of the cited page, with a
    reason per dropped one: ``citation_page_unknown`` when no chunk carries that page,
    ``citation_quote_not_in_page`` when the page's chunks do not contain the span, and
    ``citation_too_short``/``citation_too_long``/``citation_empty`` for length problems.

    This is the deterministic gate between the model and a trusted citation."""
    by_page: dict[int, str] = {}
    for chunk in chunks:
        by_page.setdefault(chunk.page, "")
        by_page[chunk.page] = by_page[chunk.page] + "\n" + chunk.text
    accepted: list[RuleCitation] = []
    dropped: list[str] = []
    seen: set[tuple[int, str]] = set()
    for citation in citations:
        collapsed = " ".join(citation.quote.split())
        if not collapsed:
            dropped.append("citation_empty")
            continue
        if len(collapsed) < MIN_CITATION_CHARS:
            dropped.append("citation_too_short")
            continue
        if len(collapsed) > MAX_CITATION_CHARS:
            dropped.append("citation_too_long")
            continue
        if citation.page not in by_page:
            dropped.append("citation_page_unknown")
            continue
        if not quote_in_text(by_page[citation.page], citation.quote):
            dropped.append("citation_quote_not_in_page")
            continue
        key = (citation.page, collapsed.casefold())
        if key in seen:
            dropped.append("citation_duplicate")
            continue
        seen.add(key)
        accepted.append(citation)
    return tuple(accepted), tuple(dropped)


REFUSAL_BELOW_SIMILARITY = "below_similarity_threshold"
REFUSAL_JUDGE_UNSUPPORTED = "judge_unsupported"
REFUSAL_NO_VALID_CITATION = "no_valid_citation"
REFUSAL_NO_CHUNKS = "no_chunks"
REFUSAL_ANSWER_MISSING = "answer_missing"


@dataclass(frozen=True)
class RefusalDecision:
    """Whether the use case must refuse, and the machine-readable reason when it does."""

    refused: bool
    reason: str | None = None


def refusal_decision(
    *,
    chunks_found: int,
    top_similarity: float | None,
    threshold: float,
    judge_supported: bool | None,
    valid_citations: int,
) -> RefusalDecision:
    """The refusal rule, in order: nothing retrieved at all, similarity below the
    threshold, the judge finding no support, or no citation that survives the verbatim
    check. Everything else is answerable."""
    if chunks_found == 0:
        return RefusalDecision(True, REFUSAL_NO_CHUNKS)
    if top_similarity is not None and top_similarity < threshold:
        return RefusalDecision(True, REFUSAL_BELOW_SIMILARITY)
    if judge_supported is not True:
        return RefusalDecision(True, REFUSAL_JUDGE_UNSUPPORTED)
    if valid_citations == 0:
        return RefusalDecision(True, REFUSAL_NO_VALID_CITATION)
    return RefusalDecision(False, None)
