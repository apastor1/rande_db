# ingestors.py
"""
Ingestors for datalake.* tables.

Provides:
- BaseIngestor: abstract base class for creating DataFile and PersonRecord rows
  with pluggable standardization rules for names and addresses.
- ExampleCsvIngestor: concrete example that reads a CSV with specific column names.

Usage (Postgres):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from ingestors import ExampleCsvIngestor

    engine = create_engine("postgresql+psycopg2://user:pass@host:5432/voter", future=True)
    Session = sessionmaker(bind=engine, future=True)

    with Session() as s:
        ing = ExampleCsvIngestor(s)
        df, info = ing.register_data_file(filename="people.csv", source="example", sha256_path="people.csv")
        if info["duplicate"]:
            print("Duplicate file found; skipping.")
        else:
            ing.ingest_rows(
                data_file=df,
                rows_iterable=ing.parse_rows_from_csv("people.csv"),
                batch_size=500
            )
"""

from __future__ import annotations

import csv
import hashlib
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from sql_orm import (
    DataFile,
    PersonRecord,
    canon_name,
    canon_addr,
    hex_md5,
)

# ----------------------
# Utility / result types
# ----------------------

@dataclass
class RegisterResult:
    created: bool
    duplicate: bool
    conflict_filename: bool
    message: str


def sha256_of_path(path: str, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# -------------
# Base ingestor
# -------------

class BaseIngestor(ABC):
    """
    Base class for ingesting a data source into datalake.* tables.

    Responsibilities:
      a) create DataFile record (with duplicate/conflict checks)
      b) create PersonRecord rows with raw payloads
      c) populate first/middle/last, name_canonical, name_hash
      d) populate street fields, address_canonical, address_hash

    Subclasses implement `standardize_name()` and `standardize_address()`
    to handle source-specific rules, and (optionally) a row parser.
    """

    def __init__(self, session: Session):
        self.session = session

    # ---- (a) DataFile registration with conflict detection ----

    def register_data_file(
        self,
        filename: str,
        source: Optional[str] = None,
        sha256: Optional[str] = None,
        sha256_path: Optional[str] = None,
        notes: Optional[str] = None,
        row_count: Optional[int] = None,
    ) -> Tuple[DataFile, Dict[str, bool]]:
        """
        Create a DataFile row, checking for duplicates by sha256 and filename conflicts.

        Returns: (data_file, info) where info contains:
            info = {
                "created": bool,
                "duplicate": bool,          # True if same sha256 already exists
                "conflict_filename": bool,  # True if filename exists but sha differs
            }
        """
        if sha256 is None and sha256_path:
            sha256 = sha256_of_path(sha256_path)

        duplicate = False
        conflict_filename = False

        # Check duplicate by sha256
        if sha256:
            existing_by_hash = self.session.execute(
                select(DataFile).where(DataFile.sha256 == sha256)
            ).scalars().first()
            if existing_by_hash:
                # Duplicate file — return existing
                return existing_by_hash, {
                    "created": False,
                    "duplicate": True,
                    "conflict_filename": False,
                }

        # Check filename conflict (same filename, different sha)
        existing_by_name = self.session.execute(
            select(DataFile).where(DataFile.filename == filename)
        ).scalars().first()
        if existing_by_name and sha256 and existing_by_name.sha256 and existing_by_name.sha256 != sha256:
            conflict_filename = True  # same name, different content

        df = DataFile(
            filename=filename,
            sha256=sha256,
            source=source,
            notes=notes,
            row_count=row_count,
        )
        self.session.add(df)
        self.session.flush()  # obtain df.id

        return df, {
            "created": True,
            "duplicate": False,
            "conflict_filename": conflict_filename,
        }

    # ---- (b) Create PersonRecord rows from raw rows ----

    def ingest_rows(
        self,
        data_file: DataFile,
        rows_iterable: Iterable[dict],
        batch_size: int = 1000,
        standardize: bool = True,
    ) -> int:
        """
        Insert PersonRecord rows for each raw dictionary in rows_iterable.
        If standardize=True, also populate standardized name/address & hashes.

        Returns: number of PersonRecord created.
        """
        count = 0
        batch: List[PersonRecord] = []

        for raw in rows_iterable:
            pr = PersonRecord(
                file_id=data_file.id,
                raw=raw,
            )

            if standardize:
                self.populate_name_fields(pr, raw)
                self.populate_address_fields(pr, raw)

            self.session.add(pr)
            batch.append(pr)
            count += 1

            if len(batch) >= batch_size:
                self.session.flush()
                batch.clear()

        self.session.commit()
        return count

    # ---- (c) Name standardization + canonical/hash ----

    def populate_name_fields(self, pr: PersonRecord, raw: dict) -> None:
        """
        Populate first/middle/last and derived canonical + hash values from raw.
        """
        first, middle, last = self.standardize_name(raw)
        pr.first_name = first
        pr.middle_name = middle
        pr.last_name = last

        # Build canonical + hash now (events also handle this at flush/commit; doing it here makes it explicit)
        pr.name_canonical = canon_name(pr.first_name, pr.middle_name, pr.last_name)
        pr.name_hash = hex_md5(pr.name_canonical)

    @abstractmethod
    def standardize_name(self, raw: dict) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Return (first_name, middle_name, last_name) extracted from raw.
        Must be implemented by subclasses.
        """
        raise NotImplementedError

    # ---- (d) Address standardization + canonical/hash ----

    def populate_address_fields(self, pr: PersonRecord, raw: dict) -> None:
        """
        Populate address fields and derived canonical + hash values from raw.
        """
        street_number, street_name, municipality, state, zip5 = self.standardize_address(raw)
        pr.street_number = street_number
        pr.street_name = street_name
        pr.municipality = municipality
        pr.state = (state or None)
        pr.zip5 = (zip5[:5] if zip5 else None)

        pr.address_canonical = canon_addr(
            pr.street_number, pr.street_name, pr.municipality, pr.state, pr.zip5
        )
        pr.address_hash = hex_md5(pr.address_canonical)

    @abstractmethod
    def standardize_address(self, raw: dict) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
        """
        Return (street_number, street_name, municipality, state, zip5) extracted from raw.
        Must be implemented by subclasses.
        """
        raise NotImplementedError

    # ---- Optional: high-level helper to ingest from a path ----

    def parse_rows_from_csv(self, path: str, encoding: str = "utf-8") -> Iterator[dict]:
        """
        Convenience parser for simple CSV sources (override if needed).
        Default: header row -> dictionaries.
        """
        with open(path, "r", encoding=encoding, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                yield dict(row)


# ----------------------
# Example concrete class
# ----------------------

class ExampleCsvIngestor(BaseIngestor):
    """
    Example of a concrete ingestor for a CSV with columns:

      Name fields:
        FirstName, MiddleName, LastName
      Address fields:
        StreetNo, StreetName, City, State, Zip

    Customize mappings in your own subclass for each vendor/source.
    """

    # Map raw keys -> our fields
    NAME_KEYS = ("FirstName", "MiddleName", "LastName")
    ADDR_KEYS = ("StreetNo", "StreetName", "City", "State", "Zip")

    def standardize_name(self, raw: dict) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        first = (raw.get(self.NAME_KEYS[0]) or "").strip() or None
        middle = (raw.get(self.NAME_KEYS[1]) or "").strip() or None
        last = (raw.get(self.NAME_KEYS[2]) or "").strip() or None
        return first, middle, last

    def standardize_address(self, raw: dict) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
        street_no = (raw.get(self.ADDR_KEYS[0]) or "").strip() or None
        street_nm = (raw.get(self.ADDR_KEYS[1]) or "").strip() or None
        city = (raw.get(self.ADDR_KEYS[2]) or "").strip() or None
        state = (raw.get(self.ADDR_KEYS[3]) or "").strip().upper() or None
        zip5 = (raw.get(self.ADDR_KEYS[4]) or "").strip() or None
        return street_no, street_nm, city, state, zip5

    # Optional: override CSV parser to coerce header names, etc.
    def parse_rows_from_csv(self, path: str, encoding: str = "utf-8") -> Iterator[dict]:
        with open(path, "r", encoding=encoding, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Normalize keys (trim spaces)
                normalized = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
                yield normalized
