"""SQLAlchemy 2 / SQLite implementation of the ProviderRepository port."""

from collections.abc import Iterable, Sequence
from datetime import date
from itertools import batched
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    Engine,
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
from ndis.domain.entities import AddressParts, Outlet, Provider, RegistrationGroup

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


class SqlAlchemyProviderRepository:
    """Stores providers, their registration groups and outlets in SQLite."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        Base.metadata.create_all(engine)

    def replace_all(self, providers: Iterable[Provider], outlets: Iterable[Outlet]) -> None:
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
            rows: Sequence[ProviderRow] = session.scalars(
                statement.order_by(ProviderRow.business_name, ProviderRow.abn)
                .limit(query.limit)
                .offset(query.offset)
            ).all()
            groups_by_abn: dict[str, list[str]] = {row.abn: [] for row in rows}
            if rows:
                group_rows = session.execute(
                    select(ProviderGroupRow.provider_abn, ProviderGroupRow.group_name)
                    .where(ProviderGroupRow.provider_abn.in_(list(groups_by_abn)))
                    .order_by(ProviderGroupRow.position)
                )
                for abn, name in group_rows:
                    groups_by_abn[abn].append(name)
            items = tuple(_to_provider(row, groups_by_abn[row.abn]) for row in rows)
        return Page(items=items, total=total, limit=query.limit, offset=query.offset)


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
