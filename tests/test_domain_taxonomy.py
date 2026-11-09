"""The taxonomy: 15 services, exact register group names, and honest bridges to the
36-item regex taxonomy of the earlier crawl."""

import pytest

from ndis.domain.taxonomy import (
    COVERED_SLUGS,
    TAXONOMY,
    TaxonomyEntry,
    entry_of,
    service_of,
    services_for_slug,
    slugs_of,
)

# The 36 slugs of the ``services`` table of the private Maps export, pinned here so a typo
# in a bridge is caught without touching private data. Each slug is the crawler's own short
# name for one kind of support.
ALL_36_SLUGS = frozenset(
    {
        "daily_living", "domestic", "community_access", "group_activities", "sil",
        "sta_respite", "mta", "ila", "transport", "nursing", "mental_health",
        "support_coordination", "plan_management", "occupational_therapy", "physiotherapy",
        "speech_pathology", "psychology", "behaviour_support", "exercise_physiology",
        "dietetics", "podiatry", "art_music_therapy", "social_work", "therapy_assistant",
        "early_childhood", "employment", "life_skills", "tenancy", "assistive_technology",
        "home_modifications", "sda", "autism", "aged_care", "cultural", "children_youth",
        "recruitment",
    }
)  # fmt: skip


def test_taxonomy_has_the_fifteen_services_in_issue_order() -> None:
    assert [entry.service.value for entry in TAXONOMY] == [
        "occupational_therapy",
        "speech_pathology",
        "psychology",
        "physiotherapy",
        "behaviour_support",
        "support_coordination",
        "plan_management",
        "supported_independent_living",
        "specialist_disability_accommodation",
        "community_access",
        "personal_care",
        "early_childhood",
        "nursing",
        "transport",
        "employment",
    ]


def test_every_entry_is_complete() -> None:
    for entry in TAXONOMY:
        assert isinstance(entry, TaxonomyEntry)
        assert entry.label and entry.description
        assert entry.registration_groups, entry.service
        assert entry.support_categories, entry.service
        assert entry.regex_slugs, entry.service


def test_every_bridged_slug_is_one_of_the_36_crawl_slugs() -> None:
    assert COVERED_SLUGS <= ALL_36_SLUGS
    assert len(COVERED_SLUGS) == 16  # 15 services; psychology also bridges mental_health


def test_registration_groups_use_the_register_vocabulary() -> None:
    names = {group for entry in TAXONOMY for group in entry.registration_groups}
    # Exact strings as the register's "Approved registration groups" column writes them.
    assert names <= {
        "Therapeutic Supports",
        "Behaviour Support",
        "Support Coordination",
        "Plan Management",
        "Supported Independent Living",
        "Daily Tasks/Shared Living",
        "Specialised Disability Accommodation",
        "Accommodation/Tenancy",
        "Participate Community",
        "Innov Community Participation",
        "Assist-Personal Activities",
        "Assist Personal Activities High",
        "Early Childhood Supports",
        "Community Nursing Care",
        "Assist-Travel/Transport",
        "Assist Access/Maintain Employ",
        "Spec Support Employ",
    }


def test_service_parsing_accepts_keys_and_rejects_garbage() -> None:
    assert service_of("plan_management") is TAXONOMY[6].service
    assert service_of(" PLAN_MANAGEMENT ") is TAXONOMY[6].service
    assert service_of("dog_grooming") is None
    assert service_of("") is None


def test_slug_bridges_round_trip() -> None:
    personal_care = service_of("personal_care")
    psychology = service_of("psychology")
    assert personal_care is not None and psychology is not None
    assert slugs_of(personal_care) == ("daily_living",)
    # A baseline hit on the personal-care slug is evidence only for personal care.
    assert services_for_slug("daily_living") == (personal_care,)
    # Psychology bridges both its own slug and the crawler's mental-health slug.
    assert services_for_slug("mental_health") == (psychology,)


def test_uncovered_slugs_map_to_no_service() -> None:
    for slug in ("sta_respite", "aged_care", "recruitment"):
        assert services_for_slug(slug) == ()


def test_no_two_services_share_a_primary_slug() -> None:
    primaries = [entry.regex_slugs[0] for entry in TAXONOMY]
    assert len(primaries) == len(set(primaries))


def test_unknown_service_lookup_fails_loudly() -> None:
    with pytest.raises(KeyError):
        entry_of(service_of("dog_grooming"))  # type: ignore[arg-type]
