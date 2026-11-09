"""``FuzzyScorer`` backed by rapidfuzz, kept out of the inner layers by the import contract."""

from rapidfuzz import fuzz


class RapidfuzzScorer:
    """Plain Indel similarity (``fuzz.ratio``) of two already-normalised names, 0–100.

    ``ratio`` is used rather than the token-set or partial variants because those score a
    register name that is merely *contained* in a Maps title (``disability care`` inside
    ``Brisbane Disability Care``) as 100, which links unrelated businesses.
    """

    def score(self, query: str, candidate: str) -> float:
        return float(fuzz.ratio(query, candidate))
