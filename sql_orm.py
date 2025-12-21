# sql_orm.py
# ---------------------------------------------------------------------
# Portable SQLAlchemy ORM models for a small datalake:
# - DataFile                    -> datalake.data_file
# - PersonRecord                -> datalake.person_record
# - Address (NEW, address_dim)  -> datalake.address
# - CensusGeocode (refactored)  -> datalake.census_geocode
# - OtherGeocode  (refactored)  -> datalake.other_geocode
#
# Key changes vs. your previous version:
# - Introduces Address dimension keyed by address_hash (PK).
# - CensusGeocode / OtherGeocode now use surrogate PK `id` (uuid string),
#   and both reference Address via FK: address_hash -> datalake.address(address_hash).
# - UniqueConstraint on CensusGeocode (address_hash, benchmark, vintage).
# - PersonRecord still owns address_canonical/address_hash; joins to geocodes are by address_hash.
#
# Notes:
# - DB-agnostic: uses generic SQLAlchemy types (String/JSON/etc.).
# - UUIDs stored as String(36) for portability.
# - JSON maps to native JSON where supported; emulated where not.
# - PersonRecord maintains canonical strings + md5 hashes via ORM events.
# - Relationships from PersonRecord to geocodes are view-only and join on address_hash.
# ---------------------------------------------------------------------

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()  # ensure env vars (if any) are available at import time

import hashlib
import re
import uuid
from datetime import datetime
from typing import Optional, List

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, foreign


# --------------------------
# Helpers & utility functions
# --------------------------

def new_uuid_str() -> str:
    """Return a uuid4 as a 36-char string."""
    return str(uuid.uuid4())


def collapse_ws(s: Optional[str]) -> str:
    """Lowercase, trim, and collapse runs of whitespace."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def canon_name(first: Optional[str], middle: Optional[str], last: Optional[str], name_suffix: Optional[str]) -> str:
    return collapse_ws(" ".join([(first or ""), (middle or ""), (last or ""), (name_suffix or "")]))


def canon_addr(
    street_number: Optional[str],
    street_name: Optional[str],
    municipality: Optional[str],
    state: Optional[str],
    zip5: Optional[str],
) -> str:
    """
    Canonical address as:
      "street_number street_name, municipality, state, zip5"
    Missing parts are omitted without leaving dangling commas.
    Lowercased with whitespace collapsed.
    """
    # Left side: "street_number street_name"
    left = collapse_ws(f"{street_number or ''} {street_name or ''}")

    # Right side: "municipality, state, zip5" (only present parts, comma-separated)
    right_parts = [p for p in (municipality, state, zip5) if p]
    right = ", ".join(collapse_ws(p) for p in right_parts)

    # Join with a comma only if both sides exist
    if left and right:
        return f"{left}, {right}"
    return left or right


def hex_md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


# --------------------------
# SQLAlchemy base
# --------------------------

class Base(DeclarativeBase):
    """Declarative base with default metadata."""
    pass


# --------------
# DataFile table
# --------------

class DataFile(Base):
    __tablename__ = "data_file"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid_str)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    source: Mapped[Optional[str]] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    row_count: Mapped[Optional[int]] = mapped_column()
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # one-to-many: DataFile -> PersonRecord
    records: Mapped[List["PersonRecord"]] = relationship(
        back_populates="file",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_data_file_received_at", "received_at"),
        {"schema": "datalake"},
    )

    def __repr__(self) -> str:
        return f"<DataFile id={self.id!s} filename={self.filename!r}>"


# ----------------
# Address dimension
# ----------------

class Address(Base):
    __tablename__ = "address"

    # Primary key is the hash itself for compact joins
    address_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    address_canonical: Mapped[str] = mapped_column(Text, nullable=False)

    # Helpful index for text lookups by canonical (optional)
    __table_args__ = (
        Index("ix_address_canonical", "address_canonical"),
        {"schema": "datalake"},
    )

    # Relationships
    person_records: Mapped[List["PersonRecord"]] = relationship(
        primaryjoin=lambda: Address.address_hash == foreign(PersonRecord.address_hash),
        viewonly=True,
        back_populates=None,
    )
    census_geocodes: Mapped[List["CensusGeocode"]] = relationship(
        back_populates="address",
        cascade="all, delete-orphan",
    )
    other_geocodes: Mapped[List["OtherGeocode"]] = relationship(
        back_populates="address",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Address hash={self.address_hash!s}>"


# ----------------
# PersonRecord table
# ----------------

class PersonRecord(Base):
    __tablename__ = "person_record"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid_str)
    file_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("datalake.data_file.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Full original row (lossless)
    raw: Mapped[dict] = mapped_column(JSON, nullable=False)

    # Standardized name
    first_name: Mapped[Optional[str]] = mapped_column(Text)
    middle_name: Mapped[Optional[str]] = mapped_column(Text)
    last_name: Mapped[Optional[str]] = mapped_column(Text)
    name_suffix: Mapped[Optional[str]] = mapped_column(Text)

    # Standardized address
    street_number: Mapped[Optional[str]] = mapped_column(Text)
    street_name: Mapped[Optional[str]] = mapped_column(Text)
    municipality: Mapped[Optional[str]] = mapped_column(Text)
    state: Mapped[Optional[str]] = mapped_column(String(2))
    zip5: Mapped[Optional[str]] = mapped_column(String(5))

    # Materialized canonical + hashes
    name_canonical: Mapped[str] = mapped_column(Text, nullable=False, default="")
    address_canonical: Mapped[str] = mapped_column(Text, nullable=False, default="")
    name_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")      # md5 hex (32 chars; 64 allows SHA switch)
    address_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # relationships
    file: Mapped[Optional[DataFile]] = relationship(back_populates="records")

    # View-only joins via address_hash (no FK PersonRecord -> Address)
    address: Mapped[Optional[Address]] = relationship(
        primaryjoin=lambda: PersonRecord.address_hash == foreign(Address.address_hash),
        viewonly=True,
    )
    census_geocodes: Mapped[List["CensusGeocode"]] = relationship(
        primaryjoin=lambda: PersonRecord.address_hash == foreign(CensusGeocode.address_hash),
        viewonly=True,
        back_populates=None,
    )
    other_geocodes: Mapped[List["OtherGeocode"]] = relationship(
        primaryjoin=lambda: PersonRecord.address_hash == foreign(OtherGeocode.address_hash),
        viewonly=True,
        back_populates=None,
    )
    ground_races: Mapped[list["GroundRace"]] = relationship(
        back_populates="person",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_person_record_file_id", "file_id"),
        Index("ix_person_record_name_hash", "name_hash"),
        Index("ix_person_record_address_hash", "address_hash"),
        Index("ix_person_record_zip5", "zip5"),
        Index("ix_person_record_state", "state"),
        Index("ix_person_record_municipality", "municipality"),
        {"schema": "datalake"},
    )

    def __repr__(self) -> str:
        return f"<PersonRecord id={self.id!s} name={self.first_name!r} {self.last_name!r} {self.name_suffix!r}>"


# -----------------
# CensusGeocode table (surrogate PK + FK to Address)
# -----------------

class CensusGeocode(Base):
    __tablename__ = "census_geocode"

    # Surrogate PK (UUID string); switch to Integer autoincrement if preferred
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid_str)

    address_hash: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("datalake.address.address_hash", ondelete="CASCADE"),
        nullable=False,
    )
    benchmark: Mapped[str] = mapped_column(Text, nullable=False)
    vintage: Mapped[str] = mapped_column(Text, nullable=False)

    geoid: Mapped[Optional[str]] = mapped_column(Text)    # e.g., Census GEOID
    result: Mapped[Optional[dict]] = mapped_column(JSON)  # raw Census response
    status: Mapped[Optional[str]] = mapped_column(Text)   # 'matched', 'no_match', 'ambiguous', etc.
    geocoded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    notes: Mapped[Optional[str]] = mapped_column(Text)

    address: Mapped[Address] = relationship(back_populates="census_geocodes")

    __table_args__ = (
        UniqueConstraint("address_hash", "benchmark", "vintage", name="uq_census_addr_bench_vint"),
        Index("ix_census_geocode_addr", "address_hash"),
        Index("ix_census_geocode_combo", "benchmark", "vintage"),
        Index("ix_census_geocode_geoid", "geoid"),
        {"schema": "datalake"},
    )

    def __repr__(self) -> str:
        return f"<CensusGeocode id={self.id!s} addr={self.address_hash!s} {self.benchmark!r}-{self.vintage!r} status={self.status!r}>"


# ----------------
# OtherGeocode table (surrogate PK + FK to Address)
# ----------------

class OtherGeocode(Base):
    __tablename__ = "other_geocode"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid_str)

    address_hash: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("datalake.address.address_hash", ondelete="CASCADE"),
        nullable=False,
    )

    geocoded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Request payload you sent to provider (DB column name remains "record")
    request: Mapped[Optional[str]] = mapped_column("record", Text)

    # Provider response + outcome
    result: Mapped[Optional[dict]] = mapped_column(JSON)
    status: Mapped[Optional[str]] = mapped_column(Text)   # 'matched', 'no_match', 'ambiguous', etc.
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # Coordinates if provided by the third-party
    latitude: Mapped[Optional[float]] = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)

    address: Mapped[Address] = relationship(back_populates="other_geocodes")

    __table_args__ = (
        Index("ix_other_geocode_addr", "address_hash"),
        Index("ix_other_geocode_status", "status"),
        {"schema": "datalake"},
    )

    def __repr__(self) -> str:
        return f"<OtherGeocode id={self.id!s} addr={self.address_hash!s} status={self.status!r}>"

# --------------
# GroundRace table
# --------------

class GroundRace(Base):
    __tablename__ = "ground_race"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid_str)

    # Which bucketing/model produced the value (e.g., "bayesian_v1", "surname_model")
    bucket_model: Mapped[str] = mapped_column(Text, nullable=False)

    # The bucketed value (e.g., "asian", "white", "black", etc.)
    value: Mapped[str] = mapped_column(Text, nullable=False)

    # FK to the person this label belongs to
    person_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("datalake.person_record.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Relationship back to PersonRecord
    person: Mapped[PersonRecord] = relationship(back_populates="ground_races")

    __table_args__ = (
        Index("ix_ground_race_person_id", "person_id"),
        Index("ix_ground_race_bucket_model", "bucket_model"),
        {"schema": "datalake"},
    )

    def __repr__(self) -> str:
        return f"<GroundRace id={self.id!s} person_id={self.person_id!s} model={self.bucket_model!r} value={self.value!r}>"


# -----------------------------------------------------
# Events to maintain canonical + hash fields (portable)
# -----------------------------------------------------

def _refresh_canon_and_hash(target: PersonRecord) -> None:
    target.name_canonical = canon_name(target.first_name, target.middle_name, target.last_name, target.name_suffix)
    target.address_canonical = canon_addr(
        target.street_number, target.street_name, target.municipality, target.state, target.zip5
    )
    target.name_hash = hex_md5(target.name_canonical)
    target.address_hash = hex_md5(target.address_canonical)


@event.listens_for(PersonRecord, "before_insert")
def _pr_before_insert(mapper, connection, target: PersonRecord) -> None:
    _refresh_canon_and_hash(target)


@event.listens_for(PersonRecord, "before_update")
def _pr_before_update(mapper, connection, target: PersonRecord) -> None:
    _refresh_canon_and_hash(target)


# ----------------
# Public exports
# ----------------

__all__ = [
    "Base",
    "DataFile",
    "PersonRecord",
    "Address",
    "CensusGeocode",
    "OtherGeocode",
    "new_uuid_str",
    "canon_name",
    "canon_addr",
    "hex_md5",
]
