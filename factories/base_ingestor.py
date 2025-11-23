# ingestors.py
from __future__ import annotations

# Always load .env first (per your preference)
from dotenv import load_dotenv
load_dotenv()

import hashlib
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

import pandas as pd
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
# Utilities
# ----------------------

@dataclass
class RegisterInfo:
    created: bool
    duplicate: bool
    conflict_filename: bool

def sha256_of_path(path: str, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()

def _norm_str(val) -> Optional[str]:
    """Normalize cell values from pandas: strip, convert NaN/empty to None."""
    if pd.isna(val):
        return None
    if isinstance(val, str):
        s = val.strip()
        return s if s else None
    # non-string (int/float/etc.)
    s = str(val).strip()
    return s if s else None


# ----------------------
# Base ingestor (pandas)
# ----------------------

class BaseIngestor(ABC):
    """
    Base class for ingesting via pandas.
      a) create DataFile (duplicate/conflict checks)
      b) add PersonRecord rows with raw payloads from DataFrame rows
      c) populate name fields + canonical/hash
      d) populate address fields + canonical/hash

    Subclasses implement:
      - standardize_name(row: dict) -> (first, middle, last)
      - standardize_address(row: dict) -> (street_number, street_name, municipality, state, zip5)
    """

    def __init__(self, session: Session):
        self.session = session

    # ---- (a) Register DataFile with checks ----

    def register_data_file(
        self,
        filename: str,
        source: Optional[str] = None,
        sha256: Optional[str] = None,
        sha256_path: Optional[str] = None,
        notes: Optional[str] = None,
        row_count: Optional[int] = None,
    ) -> Tuple[DataFile, RegisterInfo]:
        if sha256 is None and sha256_path:
            sha256 = sha256_of_path(sha256_path)

        if sha256:
            existing_by_hash = self.session.execute(
                select(DataFile).where(DataFile.sha256 == sha256)
            ).scalars().first()
            if existing_by_hash:
                return existing_by_hash, RegisterInfo(created=False, duplicate=True, conflict_filename=False)

        conflict_filename = False
        existing_by_name = self.session.execute(
            select(DataFile).where(DataFile.filename == filename)
        ).scalars().first()
        if existing_by_name and sha256 and existing_by_name.sha256 and existing_by_name.sha256 != sha256:
            conflict_filename = True

        df = DataFile(
            filename=filename,
            sha256=sha256,
            source=source,
            notes=notes,
            row_count=row_count,
        )
        self.session.add(df)
        self.session.flush()
        return df, RegisterInfo(created=True, duplicate=False, conflict_filename=conflict_filename)

    # ---- CSV loaders using pandas ----

    def load_dataframe(self, path: str, **read_csv_kwargs) -> pd.DataFrame:
        """
        Load entire CSV into memory via pandas.read_csv.
        Customize kwargs (dtype, encoding, na_values, etc.)
        """
        # sensible defaults for string-heavy data
        defaults = dict(keep_default_na=True)
        defaults.update(read_csv_kwargs or {})
        return pd.read_csv(path, **defaults)

    def load_chunks(self, path: str, chunksize: int, **read_csv_kwargs) -> Iterator[pd.DataFrame]:
        """
        Stream CSV in chunks via pandas.read_csv(..., chunksize=...).
        """
        defaults = dict(keep_default_na=True)
        defaults.update(read_csv_kwargs or {})
        reader = pd.read_csv(path, chunksize=chunksize, **defaults)
        for chunk in reader:
            yield chunk

    # ---- (b) Ingest from DataFrame ----

    def ingest_dataframe(
        self,
        data_file: DataFile,
        df: pd.DataFrame,
        batch_size: int = 1000,
        standardize: bool = True,
    ) -> int:
        """
        Insert PersonRecord rows from DataFrame. If standardize=True, also fill
        name/address + canonical + hashes.
        """
        # Convert DataFrame rows to dicts once (avoids pandas Series object reuse issues)
        rows = df.to_dict(orient="records")
        return self._ingest_rows_iterable(data_file, rows, batch_size, standardize)

    # ---- (b alt) Ingest CSV in chunks ----

    def ingest_csv(
        self,
        data_file: DataFile,
        path: str,
        chunksize: Optional[int] = None,
        batch_size: int = 1000,
        standardize: bool = True,
        **read_csv_kwargs,
    ) -> int:
        """
        Load CSV (optionally in chunks) and ingest.
        Returns total PersonRecord count inserted.
        """
        total = 0
        if chunksize and chunksize > 0:
            for chunk in self.load_chunks(path, chunksize=chunksize, **read_csv_kwargs):
                total += self.ingest_dataframe(data_file, chunk, batch_size=batch_size, standardize=standardize)
        else:
            df = self.load_dataframe(path, **read_csv_kwargs)
            total += self.ingest_dataframe(data_file, df, batch_size=batch_size, standardize=standardize)
        return total

    # ---- Core insert loop shared by both paths ----

    def _ingest_rows_iterable(
        self,
        data_file: DataFile,
        rows_iterable: Iterable[dict],
        batch_size: int,
        standardize: bool,
    ) -> int:
        count = 0
        batch: List[PersonRecord] = []

        for raw in rows_iterable:
            # Normalize raw dict to ensure None for NaN, strip strings
            normalized_raw = {k: _norm_str(v) if not isinstance(v, (dict, list)) else v for k, v in raw.items()}

            pr = PersonRecord(
                file_id=data_file.id,
                raw=normalized_raw,
            )

            if standardize:
                # (c) Name
                first, middle, last = self.standardize_name(normalized_raw)
                pr.first_name = _norm_str(first)
                pr.middle_name = _norm_str(middle)
                pr.last_name = _norm_str(last)
                pr.name_canonical = canon_name(pr.first_name, pr.middle_name, pr.last_name)
                pr.name_hash = hex_md5(pr.name_canonical)

                # (d) Address
                street_number, street_name, municipality, state, zip5 = self.standardize_address(normalized_raw)
                pr.street_number = _norm_str(street_number)
                pr.street_name = _norm_str(street_name)
                pr.municipality = _norm_str(municipality)
                pr.state = (_norm_str(state) or None)
                zip5 = _norm_str(zip5)
                pr.zip5 = zip5[:5] if zip5 else None

                pr.address_canonical = canon_addr(
                    pr.street_number, pr.street_name, pr.municipality, pr.state, pr.zip5
                )
                pr.address_hash = hex_md5(pr.address_canonical)

            self.session.add(pr)
            batch.append(pr)
            count += 1

            if len(batch) >= batch_size:
                self.session.flush()
                batch.clear()

        self.session.commit()
        return count

    # ---- Abstract standardizers ----

    @abstractmethod
    def standardize_name(self, row: dict) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Return (first, middle, last) pulled from raw row."""
        raise NotImplementedError

    @abstractmethod
    def standardize_address(self, row: dict) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
        """Return (street_number, street_name, municipality, state, zip5) pulled from raw row."""
        raise NotImplementedError


# -----------------------------------
# Example subclass (pandas + mappings)
# -----------------------------------

class ExampleCsvIngestor(BaseIngestor):
    """
    Example for a CSV with columns:
      FirstName, MiddleName, LastName, StreetNo, StreetName, City, State, Zip
    """

    NAME_COLS = {
        "first": "FirstName",
        "middle": "MiddleName",
        "last": "LastName",
    }
    ADDR_COLS = {
        "street_number": "StreetNo",
        "street_name": "StreetName",
        "municipality": "City",
        "state": "State",
        "zip5": "Zip",
    }

    def standardize_name(self, row: dict) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        first = row.get(self.NAME_COLS["first"])
        middle = row.get(self.NAME_COLS["middle"])
        last = row.get(self.NAME_COLS["last"])
        return first, middle, last

    def standardize_address(self, row: dict) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
        street_no = row.get(self.ADDR_COLS["street_number"])
        street_nm = row.get(self.ADDR_COLS["street_name"])
        city = row.get(self.ADDR_COLS["municipality"])
        state = row.get(self.ADDR_COLS["state"])
        zip5 = row.get(self.ADDR_COLS["zip5"])
        return street_no, street_nm, city, state, zip5


__all__ = ["BaseIngestor", "ExampleCsvIngestor", "RegisterInfo"]
