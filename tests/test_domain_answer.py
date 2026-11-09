"""The output guard over the composed answer: providers must be in the executed result
set, services need verbatim page evidence, and the services caveat is exact. Adversarial
fixtures (fabricated providers, reworded and zero-width quotes) must be rejected."""

from datetime import date

from ndis.domain.answer import (
    CAVEAT_SERVICES,
    AnswerDocument,
    ClaimedService,
    ProviderEntry,
    ServiceMention,
    check_answer,
    fold_claims,
    mentions_for_provider,
    uses_services,
)
from ndis.domain.entities import AddressParts, Provider, RegistrationGroup
from ndis.domain.taxonomy import Service

PAGE = (
    "Aspire Care offers speech pathology and occupational therapy for NDIS participants "
    "across Brisbane. Our speech pathologists work with children with language delays."
)
TEXTS = {("111", "https://aspire.example/"): PAGE}
QUOTE = "speech pathology and occupational therapy for NDIS participants"
assert QUOTE in " ".join(PAGE.split())


def provider(abn: str, name: str) -> Provider:
    return Provider(
        abn=abn,
        business_name=name,
        legal_name=name.upper(),
        head_office=AddressParts("WEST END", "QLD", "4101"),
        website=None,
        website_domain=None,
        registration_status="Approved",
        registration_expires_on=date(2028, 1, 1),
        registration_groups=(RegistrationGroup("Therapeutic Supports"),),
        brisbane_area=True,
    )


RESULTS = (provider("111", "Aspire Care"), provider("222", "Beta Supports"))


_UNSET = object()


def document(providers: tuple[ProviderEntry, ...], caveat: object = _UNSET) -> AnswerDocument:
    """A document whose caveat defaults to the exact sentence when the entries use
    services, and to none when they do not, so each test states only its difference."""
    if caveat is _UNSET:
        caveat = CAVEAT_SERVICES if any(entry.services for entry in providers) else None
    return AnswerDocument(providers=providers, caveat=caveat)  # type: ignore[arg-type]


class TestProviderInResults:
    def test_entries_from_the_results_pass(self) -> None:
        result = check_answer(document((ProviderEntry("111", "Aspire Care"),)), RESULTS, TEXTS)
        assert result.valid

    def test_a_fabricated_provider_is_rejected(self) -> None:
        fabricated = (ProviderEntry("999", "Fabricated Therapy Pty Ltd"),)
        result = check_answer(document(fabricated), RESULTS, TEXTS)
        assert not result.valid
        assert "provider_not_in_results" in result.violations

    def test_a_fabricated_name_on_a_real_abn_is_rejected(self) -> None:
        entries = (ProviderEntry("111", "Aspire Care Pty Ltd Trading As Something Else"),)
        result = check_answer(document(entries), RESULTS, TEXTS)
        assert not result.valid
        assert "provider_name_mismatch" in result.violations

    def test_results_are_the_whole_executed_set(self) -> None:
        entries = (ProviderEntry("222", "Beta Supports"),)
        assert check_answer(document(entries), RESULTS, TEXTS).valid


class TestEvidence:
    def entry_with(self, quote: str, url: str = "https://aspire.example/") -> ProviderEntry:
        mention = ServiceMention(Service.SPEECH_PATHOLOGY, url, quote)
        return ProviderEntry("111", "Aspire Care", (mention,))

    def test_a_verbatim_quote_passes(self) -> None:
        result = check_answer(document((self.entry_with(QUOTE),)), RESULTS, TEXTS)
        assert result.valid

    def test_a_reworded_quote_is_rejected(self) -> None:
        reworded = "speech therapy and occupational therapy for NDIS participants"
        result = check_answer(document((self.entry_with(reworded),)), RESULTS, TEXTS)
        assert not result.valid
        assert "evidence_quote_not_in_page" in result.violations

    def test_a_zero_width_injected_quote_is_rejected(self) -> None:
        poisoned = "speech patholog\u200by and occupational therapy for NDIS participants"
        result = check_answer(document((self.entry_with(poisoned),)), RESULTS, TEXTS)
        assert not result.valid
        assert "evidence_quote_not_in_page" in result.violations

    def test_a_too_short_quote_is_rejected(self) -> None:
        result = check_answer(document((self.entry_with("speech pathology"),)), RESULTS, TEXTS)
        assert not result.valid
        assert "evidence_quote_not_in_page" in result.violations

    def test_a_missing_page_is_rejected(self) -> None:
        entry = self.entry_with(QUOTE, url="https://aspire.example/lost")
        result = check_answer(document((entry,)), RESULTS, TEXTS)
        assert not result.valid
        assert "evidence_page_missing" in result.violations


class TestCaveat:
    def entry(self, services: tuple[ServiceMention, ...]) -> ProviderEntry:
        return ProviderEntry("111", "Aspire Care", services)

    def test_the_caveat_constant_is_the_exact_required_sentence(self) -> None:
        assert CAVEAT_SERVICES == (
            "the register does not record profession; services come from the provider's website"
        )

    def test_caveat_required_when_services_are_used(self) -> None:
        mention = ServiceMention(Service.SPEECH_PATHOLOGY, "https://aspire.example/", QUOTE)
        entry = self.entry((mention,))
        assert check_answer(document((entry,)), RESULTS, TEXTS).valid
        without = AnswerDocument(providers=(entry,), caveat=None)
        result = check_answer(without, RESULTS, TEXTS)
        assert not result.valid
        assert result.violations == ("caveat_missing",)

    def test_caveat_text_must_be_exact(self) -> None:
        mention = ServiceMention(Service.SPEECH_PATHOLOGY, "https://aspire.example/", QUOTE)
        paraphrased = AnswerDocument(
            providers=(self.entry((mention,)),),
            caveat="services come from provider websites; professions are not recorded",
        )
        result = check_answer(paraphrased, RESULTS, TEXTS)
        assert not result.valid
        assert result.violations == ("caveat_missing",)

    def test_caveat_forbidden_when_no_services_are_used(self) -> None:
        entry = ProviderEntry("111", "Aspire Care")
        result = check_answer(
            AnswerDocument(providers=(entry,), caveat=CAVEAT_SERVICES), RESULTS, TEXTS
        )
        assert not result.valid
        assert result.violations == ("caveat_unneeded",)
        assert check_answer(document((entry,)), RESULTS, TEXTS).valid

    def test_uses_services_is_false_for_an_empty_document(self) -> None:
        assert not uses_services(AnswerDocument())


class TestMentions:
    def test_claims_without_page_evidence_are_dropped(self) -> None:
        claims = (
            ClaimedService(Service.SPEECH_PATHOLOGY, "https://aspire.example/", QUOTE, 0.9),
            ClaimedService(
                Service.PSYCHOLOGY, "https://aspire.example/", "psychology services", 1.0
            ),
        )
        mentions = mentions_for_provider(claims, {"https://aspire.example/": PAGE})
        assert len(mentions) == 1
        assert mentions[0].service is Service.SPEECH_PATHOLOGY
        assert mentions[0].quote == QUOTE

    def test_duplicate_page_service_claims_fold_to_the_most_confident(self) -> None:
        first = ClaimedService(Service.PSYCHOLOGY, "u", "a" * 30, 0.4)
        second = ClaimedService(Service.PSYCHOLOGY, "u", "b" * 30, 0.8)
        folded = fold_claims((first, second))
        assert len(folded) == 1 and folded[0] is second

    def test_mentions_are_ordered_and_capped(self) -> None:
        claims = tuple(
            ClaimedService(
                Service(service_value),
                f"https://x.example/{i:02}",
                f"service {i:02} evidence " + "y" * 20,
                0.5,
            )
            for i, service_value in enumerate(sorted(service.value for service in Service))
        )
        assert len(claims) == len(Service)
        mentions = mentions_for_provider(
            claims,
            {claim.url: f"service {i:02} evidence " + "y" * 20 for i, claim in enumerate(claims)},
        )
        assert len(mentions) <= 12
        assert [mention.service.value for mention in mentions] == sorted(
            mention.service.value for mention in mentions
        )
