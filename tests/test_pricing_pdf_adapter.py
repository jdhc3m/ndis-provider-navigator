"""The pymupdf adapter on a small real PDF built in the fixture, and on junk files."""

from pathlib import Path

import pymupdf
import pytest

from ndis.adapters.pricing_pdf import PricingPdf, PricingPdfError, count_pdf_pages


@pytest.fixture
def mini_pdf(tmp_path: Path) -> Path:
    """A three-page PDF with a table of contents and section headings on page 3."""
    path = tmp_path / "mini.pdf"
    document = pymupdf.open()
    for index in range(3):
        page = document.new_page()
        if index == 0:
            page.insert_text((72, 72), "Mini Pricing Arrangements", fontsize=20)
        elif index == 1:
            page.insert_text((72, 72), "Contents\nIntroduction ......... 3", fontsize=12)
        else:
            page.insert_text((72, 72), "Introduction\nRules about travel and cancellations.")
    document.set_toc([[1, "Mini Pricing Arrangements", 1], [1, "Introduction", 3]])
    document.save(path)
    document.close()
    return path


def test_pages_are_one_based_and_ordered(mini_pdf: Path) -> None:
    with PricingPdf(mini_pdf) as pdf:
        pages = pdf.pages()
        assert [page.page for page in pages] == [1, 2, 3]
        assert "Rules about travel" in pages[2].text


def test_headings_come_from_the_toc(mini_pdf: Path) -> None:
    with PricingPdf(mini_pdf) as pdf:
        headings = pdf.headings()
        assert (headings[0].page, headings[0].level, headings[0].title) == (
            1,
            1,
            "Mini Pricing Arrangements",
        )
        assert headings[1].page == 3


def test_page_count_helper(mini_pdf: Path) -> None:
    assert count_pdf_pages(mini_pdf) == 3


def test_junk_file_raises_pricing_pdf_error(tmp_path: Path) -> None:
    junk = tmp_path / "junk.pdf"
    junk.write_text("<html>Just a moment...</html>", encoding="utf-8")
    with pytest.raises(PricingPdfError):
        count_pdf_pages(junk)
    with pytest.raises(PricingPdfError):
        PricingPdf(junk)
