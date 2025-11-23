# sql_orm.py
# ---------------------------------------------------------------------
# Portable SQLAlchemy ORM models for a small datalake:
# - DataFile (was: VoterFile)         -> datalake.data_file
# - PersonRecord (was: VoterRecord)   -> datalake.person_record
# - CensusGeocode (was: VoterGeocode) -> datalake.census_geocode
# - OtherGeocode (new, 3rd-party)     -> datalake.other_geocode
#
# Notes:
# - DB-agnostic: uses String/JSON, UUIDs stored as String(36).
# - JSON maps to native JSON where available; emulated on SQLite.
# - PersonRecord maintains canonical strings + md5 hashes via ORM events.
# - CensusGeocode stores one row per (record_id, benchmark, vintage).
# - OtherGeocode stores 3rd-party attempts with raw request ("record"),
#   response payload, and optional latitude/longitude.
# ---------------------------------------------------------------------

from __future__ import annotations

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
    event,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# --------------------------
# Helpers & utility functions
# --------------------------

def new_uuid_str() -> str:
    """Return a uuid4 as a 36-char string."""
    return str(uuid.uuid4())


def collapse_ws(s: Optional[str]) -> str:
    """Lowercase, trim, and collapse runs of whitespace."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def canon_name(first: Optional[str], middle: Optional[str], last: Optional[str]) -> str:
    return collapse_ws(" ".join([(first or ""), (middle or ""), (last or "")]))


def canon_addr(
    street_number: Optional[str],
    street_name: Optional[str],
    municipality: Optional[str],
    state: Optional[str],
    zip5: Optional[str],
) -> str:
    return collapse_ws(" ".join([
        (street_number or ""),
        (street_name or ""),
        (municipality or ""),
        (state or ""),
        (zip5 or ""),
    ]))


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

    # Standardized address
    street_number: Mapped[Optional[str]] = mapped_column(Text)
    street_name: Mapped[Optional[str]] = mapped_column(Text)
    municipality: Mapped[Optional[str]] = mapped_column(Text)
    state: Mapped[Optional[str]] = mapped_column(String(2))
    zip5: Mapped[Optional[str]] = mapped_column(String(5))

    # Materialized canonical + hashes
    name_canonical: Mapped[str] = mapped_column(Text, nullable=False, default="")
    address_canonical: Mapped[str] = mapped_column(Text, nullable=False, default="")
    name_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")      # md5 hex (32 chars; 64 allows easy SHA switch)
    address_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # relationships
    file: Mapped[Optional[DataFile]] = relationship(back_populates="records")

    census_geocodes: Mapped[List["CensusGeocode"]] = relationship(
        back_populates="person",
        cascade="all, delete-orphan",
    )
    other_geocodes: Mapped[List["OtherGeocode"]] = relationship(
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
        return f"<PersonRecord id={self.id!s} name={self.first_name!r} {self.last_name!r}>"


# -----------------
# CensusGeocode table
# -----------------

class CensusGeocode(Base):
    __tablename__ = "census_geocode"

    # Composite PK to capture {record_id, benchmark, vintage}
    record_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("datalake.person_record.id", ondelete="CASCADE"),
        primary_key=True,
    )
    benchmark: Mapped[str] = mapped_column(Text, primary_key=True)
    vintage: Mapped[str] = mapped_column(Text, primary_key=True)

    geoid: Mapped[Optional[str]] = mapped_column(Text)   # e.g., GEOID from Census
    result: Mapped[Optional[dict]] = mapped_column(JSON) # raw Census response
    status: Mapped[Optional[str]] = mapped_column(Text)  # 'matched', 'no_match', 'ambiguous', etc.
    geocoded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    notes: Mapped[Optional[str]] = mapped_column(Text)

    person: Mapped[PersonRecord] = relationship(back_populates="census_geocodes")

    __table_args__ = (
        Index("ix_census_geocode_combo", "benchmark", "vintage"),
        Index("ix_census_geocode_geoid", "geoid"),
        {"schema": "datalake"},
    )

    def __repr__(self) -> str:
        return f"<CensusGeocode record_id={self.record_id!s} {self.benchmark!r}-{self.vintage!r} status={self.status!r}>"


# ----------------
# OtherGeocode table (3rd-party / fallback)
# ----------------

class OtherGeocode(Base):
    __tablename__ = "other_geocode"

    # Simple surrogate PK keeps it portable across DBs
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid_str)

    # FK back to the same PersonRecord.id as CensusGeocode
    record_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("datalake.person_record.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Timestamp of this attempt
    geocoded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # You asked for a DB column literally named "record" (payload we sent).
    # To avoid clashing with relationship attribute names in Python,
    # we expose it as 'request' while the actual column name remains "record".
    request: Mapped[Optional[str]] = mapped_column("record", Text)

    # Provider response + outcome
    result: Mapped[Optional[dict]] = mapped_column(JSON)
    status: Mapped[Optional[str]] = mapped_column(Text)   # 'matched', 'no_match', 'ambiguous', etc.
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # Coordinates if provided by the third-party
    latitude: Mapped[Optional[float]] = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)

    person: Mapped[PersonRecord] = relationship(back_populates="other_geocodes")

    __table_args__ = (
        Index("ix_other_geocode_record_id", "record_id"),
        Index("ix_other_geocode_status", "status"),
        {"schema": "datalake"},
    )

    def __repr__(self) -> str:
        return f"<OtherGeocode id={self.id!s} record_id={self.record_id!s} status={self.status!r}>"


# -----------------------------------------------------
# Events to maintain canonical + hash fields (portable)
# -----------------------------------------------------

def _refresh_canon_and_hash(target: PersonRecord) -> None:
    target.name_canonical = canon_name(target.first_name, target.middle_name, target.last_name)
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
    "CensusGeocode",
    "OtherGeocode",
    "new_uuid_str",
    "canon_name",
    "canon_addr",
    "hex_md5",
]
