"""The service taxonomy a provider profile is expressed in, and its fixed bridges.

Three things are recorded per service so the rest of the pipeline never guesses:

- ``registration_groups``: the exact names the NDIS register uses for approved registration
  groups, as matched by the entity resolution (e.g. ``Assist-Travel/Transport``).
- ``support_categories``: the standard published NDIS support categories the service's
  claims fall under, as documentation for later pricing and search work.
- ``regex_slugs``: the slugs of the 36-item regex taxonomy of the earlier Maps crawl (the
  ``services`` table of the private Maps export) that mean the same thing. The regex
  baseline's hit on one of these slugs is that crawler claiming the service; the extraction
  evaluation compares the LLM against it on these bridges.

The slug strings are duplicated here deliberately: the private export stays private, and the
taxonomy is public, fixed data a unit test can pin.
"""

from dataclasses import dataclass
from enum import StrEnum

CORE_DAILY_LIFE = "Core – Assistance with Daily Life"
CB_IMPROVED_DAILY_LIVING = "Capacity Building – Improved Daily Living"
CB_IMPROVED_RELATIONSHIPS = "Capacity Building – Improved Relationships"
CB_SUPPORT_COORDINATION = "Capacity Building – Support Coordination"
CB_CHOICE_AND_CONTROL = "Capacity Building – Choice and Control (Plan Management)"
CB_SOCIAL_PARTICIPATION = "Capacity Building – Increased Social and Community Participation"
CB_FINDING_KEEPING_JOB = "Capacity Building – Finding and Keeping a Job"
CB_HEALTH_WELLBEING = "Capacity Building – Improved Health and Wellbeing"
CB_EARLY_CONNECTIONS = "Capacity Building – Early Connections (Birth to 9)"
CAPITAL_SDA = "Capital – Specialist Disability Accommodation"


class Service(StrEnum):
    """The 15 services a provider profile may claim. The value is the key used in prompts,
    labels, the cache and the database."""

    OCCUPATIONAL_THERAPY = "occupational_therapy"
    SPEECH_PATHOLOGY = "speech_pathology"
    PSYCHOLOGY = "psychology"
    PHYSIOTHERAPY = "physiotherapy"
    BEHAVIOUR_SUPPORT = "behaviour_support"
    SUPPORT_COORDINATION = "support_coordination"
    PLAN_MANAGEMENT = "plan_management"
    SUPPORTED_INDEPENDENT_LIVING = "supported_independent_living"
    SPECIALIST_DISABILITY_ACCOMMODATION = "specialist_disability_accommodation"
    COMMUNITY_ACCESS = "community_access"
    PERSONAL_CARE = "personal_care"
    EARLY_CHILDHOOD = "early_childhood"
    NURSING = "nursing"
    TRANSPORT = "transport"
    EMPLOYMENT = "employment"


@dataclass(frozen=True)
class TaxonomyEntry:
    """One service with the words of the three vocabularies it spans."""

    service: Service
    label: str
    description: str
    registration_groups: tuple[str, ...]
    support_categories: tuple[str, ...]
    regex_slugs: tuple[str, ...]


TAXONOMY: tuple[TaxonomyEntry, ...] = (
    TaxonomyEntry(
        service=Service.OCCUPATIONAL_THERAPY,
        label="Occupational therapy",
        description="an occupational therapist (OT) assesses and trains daily living skills, "
        "equipment prescriptions and home/environment assessments",
        registration_groups=("Therapeutic Supports",),
        support_categories=(CB_IMPROVED_DAILY_LIVING,),
        regex_slugs=("occupational_therapy",),
    ),
    TaxonomyEntry(
        service=Service.SPEECH_PATHOLOGY,
        label="Speech pathology",
        description="a speech pathologist treats communication, swallowing and feeding",
        registration_groups=("Therapeutic Supports",),
        support_categories=(CB_IMPROVED_DAILY_LIVING,),
        regex_slugs=("speech_pathology",),
    ),
    TaxonomyEntry(
        service=Service.PSYCHOLOGY,
        label="Psychology / counselling",
        description="a psychologist or counsellor provides assessment and therapy sessions",
        registration_groups=("Therapeutic Supports",),
        support_categories=(CB_IMPROVED_DAILY_LIVING,),
        regex_slugs=("psychology", "mental_health"),
    ),
    TaxonomyEntry(
        service=Service.PHYSIOTHERAPY,
        label="Physiotherapy",
        description="a physiotherapist treats movement, mobility and physical rehabilitation",
        registration_groups=("Therapeutic Supports",),
        support_categories=(CB_IMPROVED_DAILY_LIVING,),
        regex_slugs=("physiotherapy",),
    ),
    TaxonomyEntry(
        service=Service.BEHAVIOUR_SUPPORT,
        label="Positive behaviour support",
        description="specialist behaviour support: functional behaviour assessments, "
        "behaviour support plans, restrictive-practices work",
        registration_groups=("Behaviour Support",),
        support_categories=(CB_IMPROVED_RELATIONSHIPS,),
        regex_slugs=("behaviour_support",),
    ),
    TaxonomyEntry(
        service=Service.SUPPORT_COORDINATION,
        label="Support coordination",
        description="a support coordinator helps a participant find, connect and manage their "
        "supports (levels 1–3, including psychosocial recovery coaching)",
        registration_groups=("Support Coordination",),
        support_categories=(CB_SUPPORT_COORDINATION,),
        regex_slugs=("support_coordination",),
    ),
    TaxonomyEntry(
        service=Service.PLAN_MANAGEMENT,
        label="Plan management",
        description="a plan manager pays providers' invoices and manages the participant's "
        "NDIS funding on their behalf",
        registration_groups=("Plan Management",),
        support_categories=(CB_CHOICE_AND_CONTROL,),
        regex_slugs=("plan_management",),
    ),
    TaxonomyEntry(
        service=Service.SUPPORTED_INDEPENDENT_LIVING,
        label="Supported Independent Living (SIL)",
        description="ongoing help with daily tasks in a shared or live-in arrangement, "
        "including 24/7 group homes",
        registration_groups=("Supported Independent Living", "Daily Tasks/Shared Living"),
        support_categories=(CORE_DAILY_LIFE,),
        regex_slugs=("sil",),
    ),
    TaxonomyEntry(
        service=Service.SPECIALIST_DISABILITY_ACCOMMODATION,
        label="Specialist Disability Accommodation (SDA)",
        description="specialist housing built for extreme functional impairment or very high "
        "support needs (apartments, villas, SDA dwellings)",
        registration_groups=("Specialised Disability Accommodation", "Accommodation/Tenancy"),
        support_categories=(CAPITAL_SDA,),
        regex_slugs=("sda",),
    ),
    TaxonomyEntry(
        service=Service.COMMUNITY_ACCESS,
        label="Community access / participation",
        description="help to take part in community, social and civic activities, group "
        "programs and outings",
        registration_groups=("Participate Community", "Innov Community Participation"),
        support_categories=(
            "Core – Assistance with Social and Community Participation",
            CB_SOCIAL_PARTICIPATION,
        ),
        regex_slugs=("community_access",),
    ),
    TaxonomyEntry(
        service=Service.PERSONAL_CARE,
        label="Personal care / daily living assistance",
        description="support workers assist with showering, dressing, meals, medication and "
        "daily routines at home",
        registration_groups=("Assist-Personal Activities", "Assist Personal Activities High"),
        support_categories=(CORE_DAILY_LIFE,),
        regex_slugs=("daily_living",),
    ),
    TaxonomyEntry(
        service=Service.EARLY_CHILDHOOD,
        label="Early childhood early intervention (birth to 9)",
        description="early intervention for children under about 9: key-worker, multidisciplinary "
        "or therapist-led programs for young children",
        registration_groups=("Early Childhood Supports",),
        support_categories=(CB_EARLY_CONNECTIONS,),
        regex_slugs=("early_childhood",),
    ),
    TaxonomyEntry(
        service=Service.NURSING,
        label="Community nursing",
        description="registered nurses deliver wound care, medication, catheter and other "
        "clinical care",
        registration_groups=("Community Nursing Care",),
        support_categories=(CORE_DAILY_LIFE, CB_HEALTH_WELLBEING),
        regex_slugs=("nursing",),
    ),
    TaxonomyEntry(
        service=Service.TRANSPORT,
        label="Transport",
        description="help with travel: transport to appointments, community access driving, "
        "vehicle-based support",
        registration_groups=("Assist-Travel/Transport",),
        support_categories=("Core – Transport",),
        regex_slugs=("transport",),
    ),
    TaxonomyEntry(
        service=Service.EMPLOYMENT,
        label="Employment supports",
        description="help to find and keep a job: SLES, school-leaver employment, specialised "
        "supported employment or customised employment",
        registration_groups=("Assist Access/Maintain Employ", "Spec Support Employ"),
        support_categories=(CB_FINDING_KEEPING_JOB,),
        regex_slugs=("employment",),
    ),
)

SERVICES: tuple[Service, ...] = tuple(entry.service for entry in TAXONOMY)
_ENTRIES: dict[Service, TaxonomyEntry] = {entry.service: entry for entry in TAXONOMY}

# Every slug of the 36-item regex taxonomy of the earlier crawl that a taxonomy service maps
# onto. Slugs outside this set (sta_respite, mta, ilo, ...) are their own services this
# taxonomy deliberately does not cover, so a baseline hit on them counts for nothing here.
COVERED_SLUGS: frozenset[str] = frozenset(slug for entry in TAXONOMY for slug in entry.regex_slugs)


def entry_of(service: Service) -> TaxonomyEntry:
    """The taxonomy entry of a service."""
    return _ENTRIES[service]


def service_of(key: str) -> Service | None:
    """The service whose key string this is (case-insensitive, surrounding blanks ignored),
    or ``None`` when the key is not in the taxonomy."""
    try:
        return Service(key.strip().lower())
    except ValueError:
        return None


def slugs_of(service: Service) -> tuple[str, ...]:
    """The regex-taxonomy slugs that mean this service."""
    return entry_of(service).regex_slugs


def services_for_slug(slug: str) -> tuple[Service, ...]:
    """The taxonomy services a regex-baseline hit on ``slug`` counts as evidence for."""
    return tuple(entry.service for entry in TAXONOMY if slug in entry.regex_slugs)
