"""The pure service-name search: the alias table is pinned (it is part of the product's
contract with the user), matching is deterministic, and the ordering rule — base items
before claim-type variants, current before legacy, then item number — is exercised on
one of each."""

from datetime import date
from decimal import Decimal

from ndis.domain.entities import PriceLimits, SupportItem
from ndis.domain.service_search import (
    MAX_SHOWN_SERVICE_MATCHES,
    is_claim_type_variant,
    normalise_service_phrase,
    search_service_items,
    service_search_tokens,
)


def make(
    item_number: str,
    name: str,
    *,
    legacy: bool = False,
    national: str = "193.99",
) -> SupportItem:
    return SupportItem(
        item_number=item_number,
        name=name,
        registration_group_number="0128",
        registration_group_name="Therapeutic Supports",
        unit="H",
        quotable=False,
        start_date=date(2026, 7, 1),
        end_date=None,
        limits=PriceLimits(
            national=Decimal(national),
            remote=None,
            very_remote=None,
        ),
        legacy=legacy,
    )


class TestNormalisation:
    def test_case_spacing_and_punctuation_collapse(self) -> None:
        assert normalise_service_phrase("Occupational  Therapy!") == "occupational therapy"
        assert normalise_service_phrase("OT") == "ot"
        assert normalise_service_phrase("  Speech-Pathologist. ") == "speech pathologist"

    def test_the_alias_table_translates_the_words_people_use(self) -> None:
        """Pinned: the catalogue writes "Occupational Therapist", "Speech Pathologist",
        "Physiotherapist" and "Psychologist", so these aliases are what make "OT",
        "speech therapy", "physio" and "psychology" match at all."""
        assert service_search_tokens("OT") == ("occupational", "therapist")
        assert service_search_tokens("occupational therapy") == ("occupational", "therapist")
        assert service_search_tokens("speech therapy") == ("speech", "pathologist")
        assert service_search_tokens("speech pathology") == ("speech", "pathologist")
        assert service_search_tokens("physio") == ("physiotherapist",)
        assert service_search_tokens("physiotherapy") == ("physiotherapist",)
        assert service_search_tokens("psychology") == ("psychologist",)
        assert service_search_tokens("support coordination") == ("support", "coordination")
        assert service_search_tokens("plan management") == ("plan", "management")

    def test_the_words_the_catalogue_does_not_use_match_nothing_by_substring(self) -> None:
        """The reason the alias table exists: without it these find nothing."""
        assert service_search_tokens("dolphin") == ("dolphin",)
        assert service_search_tokens("OT") != ("ot",)

    def test_a_blank_or_punctuation_only_phrase_has_no_tokens(self) -> None:
        assert service_search_tokens("") == ()
        assert service_search_tokens("   ") == ()
        assert service_search_tokens("!!!") == ()


class TestMatching:
    def test_a_name_containing_the_token_sequence_in_order_matches(self) -> None:
        items = [
            make("01_661_0128_1_3", "Therapy - Occupational Therapist - Direct Service"),
            make("15_617_0128_1_3", "Therapy - Occupational Therapist - Direct Service"),
            make("01_002_0107_1_1", "Assistance With Self-Care Activities - Weekday Night"),
        ]
        search = search_service_items("occupational therapy", items)
        assert [item.item_number for item in search.matches] == [
            "01_661_0128_1_3",
            "15_617_0128_1_3",
        ]
        assert search.total == 2

    def test_the_phrase_is_matched_in_order_not_as_a_bag_of_words(self) -> None:
        """ "management plan" must not match "Behaviour Management Plan Implementation":
        the tokens must appear in the phrase's order."""
        items = [make("11_023_0110_7_3", "Behaviour Management Plan Implementation")]
        assert search_service_items("management plan", items).total == 1
        assert search_service_items("plan management", items).total == 0

    def test_a_phrase_no_name_contains_matches_nothing(self) -> None:
        items = [make("01_661_0128_1_3", "Therapy - Occupational Therapist - Direct Service")]
        assert search_service_items("underwater basket weaving", items).matches == ()
        assert search_service_items("underwater basket weaving", items).total == 0

    def test_a_blank_phrase_matches_nothing(self) -> None:
        assert search_service_items("   ", [make("01_661_0128_1_3", "anything")]).matches == ()

    def test_matching_is_case_and_punctuation_insensitive_on_both_sides(self) -> None:
        items = [make("07_001_0106_8_3", "Support Coordination Level 1: Support Connection")]
        search = search_service_items("SUPPORT  coordination?", items)
        assert search.total == 1 and search.phrase == "SUPPORT coordination?"


class TestOrdering:
    def test_base_items_before_variants_current_before_legacy_then_number(self) -> None:
        """One base item, one ``_PT`` claim-type variant and one legacy item, all with
        matching names. The rule is a sort key read left to right: base items before
        claim-type variants first, current before legacy second, then the item number —
        so the legacy base row comes before the current Provider Travel variant."""
        items = [
            make("15_055_0128_1_3_PT", "Therapy - Physiotherapist - Provider Travel"),
            make("01_045_0115_1_1", "Therapy - Physiotherapist - Direct Service", legacy=True),
            make("01_721_0128_1_3", "Therapy - Physiotherapist - Direct Service"),
        ]
        search = search_service_items("physiotherapist", items)
        assert [item.item_number for item in search.matches] == [
            "01_721_0128_1_3",
            "01_045_0115_1_1",
            "15_055_0128_1_3_PT",
        ]

    def test_bases_sort_by_item_number(self) -> None:
        items = [
            make("15_617_0128_1_3", "Therapy - Occupational Therapist - Direct Service"),
            make("01_650_0118_1_3", "ECI Professional - Occupational Therapist - Direct Service"),
            make("10_617_0128_5_3", "Employment Therapy - Occupational Therapist - Direct Service"),
        ]
        search = search_service_items("OT", items)
        assert [item.item_number for item in search.matches] == [
            "01_650_0118_1_3",
            "10_617_0128_5_3",
            "15_617_0128_1_3",
        ]

    def test_a_digit_suffixed_variant_is_still_a_variant(self) -> None:
        """The catalogue numbers some variants ``_CA2``/``_NF2``; they order after base
        items too."""
        items = [
            make("01_700_0118_1_3_CA2", "ECI Professional - Psychologist - Cancellation"),
            make("01_700_0118_1_3", "ECI Professional - Psychologist - Direct Service"),
        ]
        search = search_service_items("psychologist", items)
        assert [item.item_number for item in search.matches] == [
            "01_700_0118_1_3",
            "01_700_0118_1_3_CA2",
        ]


class TestVariantFlag:
    def test_letter_suffixes_are_variants_and_digit_ending_numbers_are_not(self) -> None:
        assert is_claim_type_variant("15_056_0128_1_3_PT")
        assert is_claim_type_variant("01_700_0118_1_3_CA2")
        assert not is_claim_type_variant("15_056_0128_1_3")
        assert not is_claim_type_variant("03_040000919_0103_1_1")


def test_the_answer_cap_is_twelve() -> None:
    """Pinned so a broad phrase cannot flood the price column; /ask shows at most this
    many matches and states the total."""
    assert MAX_SHOWN_SERVICE_MATCHES == 12
