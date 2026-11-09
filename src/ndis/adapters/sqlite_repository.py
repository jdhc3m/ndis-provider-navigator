"""SQLAlchemy 2 / SQLite implementations of the ProviderRepository, SupportItemRepository,
ProviderLinkRepository, ProviderSiteSource and PageStore ports; all share one metadata so a
single ``data/ndis.db`` holds the register, the catalogue, the Maps-to-register links and
the public-safe page metadata. Page text itself is written to files under the private
pages directory, never to the database."""

import json
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal
from itertools import batched
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    Engine,
    Float,
    ForeignKey,
    Index,
    Integer,
    Select,
    String,
    create_engine,
    delete,
    exists,
    func,
    insert,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from ndis.application.providers import Page, ProviderQuery
from ndis.domain.entities import (
    AddressParts,
    Outlet,
    PriceLimits,
    Provider,
    RegistrationGroup,
    SupportItem,
)
from ndis.domain.entity_resolution import (
    Candidate,
    EntityMatch,
    MapsPlace,
    MatchStatus,
    MatchStrategy,
    ProviderLink,
    normalise_name,
)
from ndis.domain.pages import FetchStatus, PageMeta, PageSource, ProviderSite

INSERT_BATCH_SIZE = 1000


class Base(DeclarativeBase):
    pass


class ProviderRow(Base):
    __tablename__ = "providers"

    abn: Mapped[str] = mapped_column(String(11), primary_key=True)
    business_name: Mapped[str] = mapped_column(String)
    legal_name: Mapped[str] = mapped_column(String)
    head_office_suburb: Mapped[str | None] = mapped_column(String)
    head_office_state: Mapped[str | None] = mapped_column(String(3))
    head_office_postcode: Mapped[str | None] = mapped_column(String(4))
    website: Mapped[str | None] = mapped_column(String)
    website_domain: Mapped[str | None] = mapped_column(String)
    registration_status: Mapped[str] = mapped_column(String)
    registration_expires_on: Mapped[date] = mapped_column(Date)
    brisbane_area: Mapped[bool] = mapped_column(Boolean)

    __table_args__ = (
        Index("ix_providers_state", "head_office_state"),
        Index("ix_providers_postcode", "head_office_postcode"),
        Index("ix_providers_status", "registration_status"),
        Index("ix_providers_brisbane", "brisbane_area"),
        Index("ix_providers_domain", "website_domain"),
        Index("ix_providers_name", "business_name", "abn"),
    )


class ProviderGroupRow(Base):
    __tablename__ = "provider_registration_groups"

    provider_abn: Mapped[str] = mapped_column(
        ForeignKey("providers.abn", ondelete="CASCADE"), primary_key=True
    )
    group_name: Mapped[str] = mapped_column(String, primary_key=True)
    position: Mapped[int] = mapped_column(Integer)

    __table_args__ = (Index("ix_groups_name", "group_name"),)


class OutletRow(Base):
    __tablename__ = "outlets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider_abn: Mapped[str] = mapped_column(ForeignKey("providers.abn", ondelete="CASCADE"))
    name: Mapped[str | None] = mapped_column(String)
    address_raw: Mapped[str | None] = mapped_column(String)
    suburb: Mapped[str | None] = mapped_column(String)
    state: Mapped[str | None] = mapped_column(String(3))
    postcode: Mapped[str | None] = mapped_column(String(4))
    phone: Mapped[str | None] = mapped_column(String)
    brisbane_area: Mapped[bool] = mapped_column(Boolean)

    __table_args__ = (
        Index("ix_outlets_provider", "provider_abn"),
        Index("ix_outlets_state", "state"),
        Index("ix_outlets_postcode", "postcode"),
    )


class SupportItemRow(Base):
    """Support catalogue row. Prices are stored as the exact decimal text (``"82.57"``) so
    SQLite never coerces them to floating point; they are only ever read back as Decimal."""

    __tablename__ = "support_items"

    item_number: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    registration_group_number: Mapped[str] = mapped_column(String(4))
    registration_group_name: Mapped[str] = mapped_column(String)
    unit: Mapped[str] = mapped_column(String)
    quotable: Mapped[bool] = mapped_column(Boolean)
    legacy: Mapped[bool] = mapped_column(Boolean)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    national: Mapped[str | None] = mapped_column(String)
    remote: Mapped[str | None] = mapped_column(String)
    very_remote: Mapped[str | None] = mapped_column(String)

    __table_args__ = (Index("ix_support_items_group", "registration_group_number"),)


class ProviderLinkRow(Base):
    """One resolved Maps place. ``abn`` is unset for ambiguous, unregistered and non-provider
    rows; ``candidates`` is a JSON list of ``[abn, name]`` pairs for ambiguous rows;
    latitude/longitude are kept so later tickets can rank by exact coordinates."""

    __tablename__ = "provider_links"

    place_id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String)
    category: Mapped[str | None] = mapped_column(String)
    website: Mapped[str | None] = mapped_column(String)
    website_domain: Mapped[str | None] = mapped_column(String)
    suburb: Mapped[str | None] = mapped_column(String)
    postcode: Mapped[str | None] = mapped_column(String(4))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    abn: Mapped[str | None] = mapped_column(String(11))
    match_status: Mapped[str] = mapped_column(String)
    strategy: Mapped[str] = mapped_column(String)
    confidence: Mapped[float] = mapped_column(Float)
    matched_name: Mapped[str | None] = mapped_column(String)
    candidates: Mapped[str] = mapped_column(String)

    __table_args__ = (
        Index("ix_provider_links_abn", "abn"),
        Index("ix_provider_links_status", "match_status"),
    )


class PageMetaRow(Base):
    """Public-safe record of one collected page: no text, only where it is (``text_path``
    relative to the private pages directory) and what it hashes to. ``fetched_at`` is kept
    as ISO-8601 text so the UTC offset survives SQLite."""

    __tablename__ = "page_meta"

    provider_key: Mapped[str] = mapped_column(String, primary_key=True)
    url: Mapped[str] = mapped_column(String, primary_key=True)
    fetched_at: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    source: Mapped[str] = mapped_column(String)
    text_hash: Mapped[str | None] = mapped_column(String(64))
    text_path: Mapped[str | None] = mapped_column(String)
    title: Mapped[str | None] = mapped_column(String)

    __table_args__ = (Index("ix_page_meta_status", "status"),)


def create_sqlite_engine(path: Path) -> Engine:
    """Create an engine for the SQLite file at ``path``, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}")


def _provider_values(provider: Provider) -> dict[str, object]:
    return {
        "abn": provider.abn,
        "business_name": provider.business_name,
        "legal_name": provider.legal_name,
        "head_office_suburb": provider.head_office.suburb,
        "head_office_state": provider.head_office.state,
        "head_office_postcode": provider.head_office.postcode,
        "website": provider.website,
        "website_domain": provider.website_domain,
        "registration_status": provider.registration_status,
        "registration_expires_on": provider.registration_expires_on,
        "brisbane_area": provider.brisbane_area,
    }


def _outlet_values(outlet: Outlet) -> dict[str, object]:
    return {
        "provider_abn": outlet.provider_abn,
        "name": outlet.name,
        "address_raw": outlet.address_raw,
        "suburb": outlet.address.suburb,
        "state": outlet.address.state,
        "postcode": outlet.address.postcode,
        "phone": outlet.phone,
        "brisbane_area": outlet.brisbane_area,
    }


def _to_provider(row: ProviderRow, groups: Sequence[str]) -> Provider:
    return Provider(
        abn=row.abn,
        business_name=row.business_name,
        legal_name=row.legal_name,
        head_office=AddressParts(
            suburb=row.head_office_suburb,
            state=row.head_office_state,
            postcode=row.head_office_postcode,
        ),
        website=row.website,
        website_domain=row.website_domain,
        registration_status=row.registration_status,
        registration_expires_on=row.registration_expires_on,
        registration_groups=tuple(RegistrationGroup(name) for name in groups),
        brisbane_area=row.brisbane_area,
    )


def _load_providers(
    session: Session, statement: Select[tuple[ProviderRow]]
) -> tuple[Provider, ...]:
    """Materialise provider rows with their groups, keeping the statement's order."""
    rows: Sequence[ProviderRow] = session.scalars(statement).all()
    groups_by_abn: dict[str, list[str]] = {row.abn: [] for row in rows}
    if rows:
        group_rows = session.execute(
            select(ProviderGroupRow.provider_abn, ProviderGroupRow.group_name)
            .where(ProviderGroupRow.provider_abn.in_(list(groups_by_abn)))
            .order_by(ProviderGroupRow.position)
        )
        for abn, name in group_rows:
            groups_by_abn[abn].append(name)
    return tuple(_to_provider(row, groups_by_abn[row.abn]) for row in rows)


class SqlAlchemyProviderRepository:
    """Stores providers, their registration groups and outlets in SQLite.

    Normalised names are not stored: the name index is built in memory from every row's
    business and legal name on first use and dropped again by ``replace_all``.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._name_index: dict[str, list[str]] | None = None
        Base.metadata.create_all(engine)

    def replace_all(self, providers: Iterable[Provider], outlets: Iterable[Outlet]) -> None:
        self._name_index = None
        with Session(self._engine) as session, session.begin():
            session.execute(delete(OutletRow))
            session.execute(delete(ProviderGroupRow))
            session.execute(delete(ProviderRow))
            for batch in batched(providers, INSERT_BATCH_SIZE):
                session.execute(insert(ProviderRow), [_provider_values(p) for p in batch])
                group_values = [
                    {"provider_abn": provider.abn, "group_name": group.name, "position": position}
                    for provider in batch
                    for position, group in enumerate(provider.registration_groups)
                ]
                if group_values:
                    session.execute(insert(ProviderGroupRow), group_values)
            for outlet_batch in batched(outlets, INSERT_BATCH_SIZE):
                session.execute(insert(OutletRow), [_outlet_values(o) for o in outlet_batch])

    def find(self, query: ProviderQuery) -> Page[Provider]:
        statement = _apply_filters(select(ProviderRow), query)
        with Session(self._engine) as session:
            total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
            items = _load_providers(
                session,
                statement.order_by(ProviderRow.business_name, ProviderRow.abn)
                .limit(query.limit)
                .offset(query.offset),
            )
        return Page(items=items, total=total, limit=query.limit, offset=query.offset)

    def find_by_website_domain(self, domain: str) -> tuple[Provider, ...]:
        with Session(self._engine) as session:
            return _load_providers(
                session,
                select(ProviderRow)
                .where(ProviderRow.website_domain == domain)
                .order_by(ProviderRow.abn),
            )

    def find_by_normalised_name(self, name: str) -> tuple[Provider, ...]:
        if self._name_index is None:
            self._name_index = self._build_name_index()
        abns = self._name_index.get(name, [])
        if not abns:
            return ()
        with Session(self._engine) as session:
            return _load_providers(
                session,
                select(ProviderRow).where(ProviderRow.abn.in_(abns)).order_by(ProviderRow.abn),
            )

    def brisbane_providers(self) -> tuple[Provider, ...]:
        with Session(self._engine) as session:
            return _load_providers(
                session,
                select(ProviderRow)
                .where(ProviderRow.brisbane_area.is_(True))
                .order_by(ProviderRow.abn),
            )

    def _build_name_index(self) -> dict[str, list[str]]:
        index: dict[str, list[str]] = {}
        with Session(self._engine) as session:
            rows = session.execute(
                select(ProviderRow.abn, ProviderRow.business_name, ProviderRow.legal_name)
            )
            for abn, business_name, legal_name in rows:
                for key in {normalise_name(business_name), normalise_name(legal_name)}:
                    if key:
                        index.setdefault(key, []).append(abn)
        return index


def _link_values(link: ProviderLink) -> dict[str, object]:
    place, match = link.place, link.match
    return {
        "place_id": place.place_id,
        "title": place.title,
        "category": place.category,
        "website": place.website,
        "website_domain": place.website_domain,
        "suburb": place.suburb,
        "postcode": place.postcode,
        "latitude": place.latitude,
        "longitude": place.longitude,
        "abn": match.abn,
        "match_status": match.match_status.value,
        "strategy": match.strategy.value,
        "confidence": match.confidence,
        "matched_name": match.matched_name,
        "candidates": json.dumps([[c.abn, c.name] for c in match.candidates]),
    }


def _to_link(row: ProviderLinkRow) -> ProviderLink:
    return ProviderLink(
        place=MapsPlace(
            place_id=row.place_id,
            title=row.title,
            category=row.category,
            website=row.website,
            website_domain=row.website_domain,
            suburb=row.suburb,
            postcode=row.postcode,
            latitude=row.latitude,
            longitude=row.longitude,
        ),
        match=EntityMatch(
            place_id=row.place_id,
            abn=row.abn,
            match_status=MatchStatus(row.match_status),
            strategy=MatchStrategy(row.strategy),
            confidence=row.confidence,
            matched_name=row.matched_name,
            candidates=tuple(Candidate(abn, name) for abn, name in json.loads(row.candidates)),
        ),
    )


class SqlAlchemyProviderLinkRepository:
    """Stores the resolved Maps-to-register links in SQLite, one row per place id."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        Base.metadata.create_all(engine)

    def replace_all(self, links: Iterable[ProviderLink]) -> None:
        with Session(self._engine) as session, session.begin():
            session.execute(delete(ProviderLinkRow))
            for batch in batched(links, INSERT_BATCH_SIZE):
                session.execute(insert(ProviderLinkRow), [_link_values(link) for link in batch])

    def all(self) -> tuple[ProviderLink, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(select(ProviderLinkRow).order_by(ProviderLinkRow.place_id))
            return tuple(_to_link(row) for row in rows)


class SqlAlchemyProviderSiteSource:
    """``ProviderSiteSource``: every link left-joined to its register provider's website."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        Base.metadata.create_all(engine)

    def sites(self) -> tuple[ProviderSite, ...]:
        statement = (
            select(
                ProviderLinkRow.place_id,
                ProviderLinkRow.abn,
                ProviderLinkRow.match_status,
                ProviderRow.website,
                ProviderLinkRow.website,
            )
            .join(ProviderRow, ProviderRow.abn == ProviderLinkRow.abn, isouter=True)
            .order_by(ProviderLinkRow.place_id)
        )
        with Session(self._engine) as session:
            return tuple(
                ProviderSite(place_id, abn, MatchStatus(status), register_website, maps_website)
                for place_id, abn, status, register_website, maps_website in session.execute(
                    statement
                )
            )


def _page_meta_values(meta: PageMeta) -> dict[str, object]:
    return {
        "provider_key": meta.provider_key,
        "url": meta.url,
        "fetched_at": meta.fetched_at.isoformat(),
        "status": meta.status.value,
        "source": meta.source.value,
        "text_hash": meta.text_hash,
        "text_path": meta.text_path,
        "title": meta.title,
    }


def _to_page_meta(row: PageMetaRow) -> PageMeta:
    return PageMeta(
        url=row.url,
        provider_key=row.provider_key,
        fetched_at=datetime.fromisoformat(row.fetched_at),
        status=FetchStatus(row.status),
        source=PageSource(row.source),
        text_hash=row.text_hash,
        text_path=row.text_path,
        title=row.title,
    )


class FilePageStore:
    """``PageStore``: text files under ``pages_dir`` (which must stay ignored by Git) and
    ``page_meta`` rows in SQLite, one per ``(provider_key, url)``, latest write wins."""

    def __init__(self, engine: Engine, pages_dir: Path) -> None:
        self._engine = engine
        self._pages_dir = pages_dir
        Base.metadata.create_all(engine)

    def save(self, meta: PageMeta, text: str | None) -> None:
        if text is not None and meta.text_path is not None:
            self._pages_dir.mkdir(parents=True, exist_ok=True)
            (self._pages_dir / meta.text_path).write_text(text, encoding="utf-8")
        with Session(self._engine) as session, session.begin():
            session.execute(
                delete(PageMetaRow).where(
                    PageMetaRow.provider_key == meta.provider_key, PageMetaRow.url == meta.url
                )
            )
            session.execute(insert(PageMetaRow), [_page_meta_values(meta)])

    def providers_with_ok_pages(self) -> frozenset[str]:
        with Session(self._engine) as session:
            keys = session.scalars(
                select(PageMetaRow.provider_key)
                .where(PageMetaRow.status == FetchStatus.OK.value)
                .distinct()
            )
            return frozenset(keys)

    def all(self) -> tuple[PageMeta, ...]:
        """Every stored row ordered by provider key then URL."""
        with Session(self._engine) as session:
            rows = session.scalars(
                select(PageMetaRow).order_by(PageMetaRow.provider_key, PageMetaRow.url)
            )
            return tuple(_to_page_meta(row) for row in rows)


def _apply_filters(statement: Select[Any], query: ProviderQuery) -> Select[Any]:
    if query.state is not None:
        at_outlet = exists().where(
            OutletRow.provider_abn == ProviderRow.abn, OutletRow.state == query.state
        )
        statement = statement.where((ProviderRow.head_office_state == query.state) | at_outlet)
    if query.postcode is not None:
        at_outlet = exists().where(
            OutletRow.provider_abn == ProviderRow.abn, OutletRow.postcode == query.postcode
        )
        statement = statement.where(
            (ProviderRow.head_office_postcode == query.postcode) | at_outlet
        )
    if query.group is not None:
        statement = statement.where(
            exists().where(
                ProviderGroupRow.provider_abn == ProviderRow.abn,
                ProviderGroupRow.group_name == query.group,
            )
        )
    if query.status is not None:
        statement = statement.where(ProviderRow.registration_status == query.status)
    if query.brisbane_area is not None:
        statement = statement.where(ProviderRow.brisbane_area.is_(query.brisbane_area))
    return statement


def _money_text(amount: Decimal | None) -> str | None:
    return None if amount is None else str(amount)


def _money(text: str | None) -> Decimal | None:
    return None if text is None else Decimal(text)


def _support_item_values(item: SupportItem) -> dict[str, object]:
    return {
        "item_number": item.item_number,
        "name": item.name,
        "registration_group_number": item.registration_group_number,
        "registration_group_name": item.registration_group_name,
        "unit": item.unit,
        "quotable": item.quotable,
        "legacy": item.legacy,
        "start_date": item.start_date,
        "end_date": item.end_date,
        "national": _money_text(item.limits.national),
        "remote": _money_text(item.limits.remote),
        "very_remote": _money_text(item.limits.very_remote),
    }


def _to_support_item(row: SupportItemRow) -> SupportItem:
    return SupportItem(
        item_number=row.item_number,
        name=row.name,
        registration_group_number=row.registration_group_number,
        registration_group_name=row.registration_group_name,
        unit=row.unit,
        quotable=row.quotable,
        start_date=row.start_date,
        end_date=row.end_date,
        limits=PriceLimits(
            national=_money(row.national),
            remote=_money(row.remote),
            very_remote=_money(row.very_remote),
        ),
        legacy=row.legacy,
    )


class SqlAlchemySupportItemRepository:
    """Stores the support catalogue in SQLite, one row per support item number."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        Base.metadata.create_all(engine)

    def replace_all(self, items: Iterable[SupportItem]) -> None:
        with Session(self._engine) as session, session.begin():
            session.execute(delete(SupportItemRow))
            for batch in batched(items, INSERT_BATCH_SIZE):
                session.execute(insert(SupportItemRow), [_support_item_values(i) for i in batch])

    def get(self, item_number: str) -> SupportItem | None:
        with Session(self._engine) as session:
            row = session.get(SupportItemRow, item_number)
            return None if row is None else _to_support_item(row)
