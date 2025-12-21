# ingestors.py
from __future__ import annotations

# Always load .env first (per your preference)
from dotenv import load_dotenv
load_dotenv()

import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable, Iterator, List, Optional, Tuple

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from factories.street_parser import StreetParser

from sql_orm import (
    DataFile,
    PersonRecord,
    GroundRace,
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


class Helper:
    @staticmethod
    def parse_street_address(addr: str) -> Tuple[Optional[str], str]:
        """
        Split a street address into leading numeric part and the remainder.
        '100 Main Str' -> ('100', 'Main Str'); 'Main Str' -> (None, 'Main Str')
        """
        s = (addr or "").strip()
        m = re.match(r"^(\d+)\b\s*(.*)$", s)
        if m:
            number = m.group(1)
            rest = m.group(2).strip()
            return number, rest
        return None, s

    @staticmethod
    def sha256_of_path(path: str, chunk_size: int = 1024 * 1024) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _norm_str(val) -> Optional[str]:
        """Normalize cell values from pandas: strip, convert NaN/empty to None."""
        if pd.isna(val):
            return None
        if isinstance(val, str):
            s = val.strip()
            return s if s else None
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
      e) (optional) populate GroundRace inline when race_bucket_model is provided

    Subclasses implement:
      - standardize_name(row: dict) -> (first, middle, last, name_suffix)
      - standardize_address(row: dict) -> (street_number, street_name, municipality, state, zip5)
      - update_ground_race(person: PersonRecord, *, bucket_model: Optional[str]) -> Optional[Tuple[str, str]]
        (Return (bucket_model, value) or None to skip.)
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
    ) -> Tuple[DataFile, RegisterInfo]:
        if sha256 is None and sha256_path:
            sha256 = Helper.sha256_of_path(sha256_path)

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
            row_count=None,  # will be filled after ingest
        )
        self.session.add(df)
        self.session.flush()
        return df, RegisterInfo(created=True, duplicate=False, conflict_filename=conflict_filename)

    # ---- CSV loaders using pandas (no extra kwargs) ----

    def load_dataframe(self, path: str) -> pd.DataFrame:
        """Load entire CSV into memory via pandas.read_csv (default params)."""
        return pd.read_csv(path)

    def load_chunks(self, path: str, chunksize: int) -> Iterator[pd.DataFrame]:
        """Stream CSV in chunks via pandas.read_csv(..., chunksize=...)."""
        reader = pd.read_csv(path, chunksize=chunksize)
        for chunk in reader:
            yield chunk

    # ---- (b) Ingest from DataFrame ----

    def ingest_dataframe(
        self,
        data_file: DataFile,
        df: pd.DataFrame,
        batch_size: int = 1000,
        standardize: bool = True,
        race_bucket_model: Optional[str] = None,
    ) -> int:
        """
        Insert PersonRecord rows from DataFrame. If standardize=True, also fill
        name/address + canonical + hashes. If race_bucket_model is set, compute
        and store GroundRace inline per person.
        """
        rows = df.to_dict(orient="records")
        return self._ingest_rows_iterable(
            data_file,
            rows,
            batch_size=batch_size,
            standardize=standardize,
            race_bucket_model=race_bucket_model,
        )

    # ---- (b alt) Ingest CSV in chunks ----

    def ingest_csv(
        self,
        data_file: DataFile,
        path: str,
        chunksize: Optional[int] = None,
        batch_size: int = 1000,
        standardize: bool = True,
        race_bucket_model: Optional[str] = None,
    ) -> int:
        """
        Load CSV (optionally in chunks) and ingest.
        Returns total PersonRecord count inserted.
        """
        total = 0
        if chunksize and chunksize > 0:
            for chunk in self.load_chunks(path, chunksize=chunksize):
                total += self.ingest_dataframe(
                    data_file,
                    chunk,
                    batch_size=batch_size,
                    standardize=standardize,
                    race_bucket_model=race_bucket_model,
                )
        else:
            df = self.load_dataframe(path)
            total += self.ingest_dataframe(
                data_file,
                df,
                batch_size=batch_size,
                standardize=standardize,
                race_bucket_model=race_bucket_model,
            )
        data_file.row_count = total
        self.session.commit()
        return total

    # ---- Core insert loop shared by both paths ----

    def _ingest_rows_iterable(
        self,
        data_file: DataFile,
        rows_iterable: Iterable[dict],
        batch_size: int,
        standardize: bool,
        race_bucket_model: Optional[str],
    ) -> int:
        count = 0
        batch: List[PersonRecord] = []
        gr_buffer: List[Tuple[PersonRecord, str, str]] = []  # (person, bucket_model, value)

        for raw in rows_iterable:
            # Normalize raw dict to ensure None for NaN, strip strings
            normalized_raw = {
                k: Helper._norm_str(v) if not isinstance(v, (dict, list)) else v
                for k, v in raw.items()
            }

            pr = PersonRecord(file_id=data_file.id, raw=normalized_raw)

            if standardize:
                # (c) Name
                first, middle, last, name_suffix = self.standardize_name(normalized_raw)
                pr.first_name = Helper._norm_str(first)
                pr.middle_name = Helper._norm_str(middle)
                pr.last_name = Helper._norm_str(last)
                pr.name_suffix = Helper._norm_str(name_suffix)
                pr.name_canonical = canon_name(pr.first_name, pr.middle_name, pr.last_name, pr.name_suffix)
                pr.name_hash = hex_md5(pr.name_canonical)

                # (d) Address
                street_number, street_name, municipality, state, zip5 = self.standardize_address(normalized_raw)
                pr.street_number = Helper._norm_str(street_number)
                pr.street_name = Helper._norm_str(street_name)
                pr.municipality = Helper._norm_str(municipality)
                pr.state = Helper._norm_str(state) or None
                zip5 = Helper._norm_str(zip5)
                pr.zip5 = zip5[:5] if zip5 else None

                pr.address_canonical = canon_addr(
                    pr.street_number, pr.street_name, pr.municipality, pr.state, pr.zip5
                )
                pr.address_hash = hex_md5(pr.address_canonical)

            self.session.add(pr)
            batch.append(pr)
            count += 1

            # Inline GroundRace (buffer until we flush PersonRecord to get IDs)
            if race_bucket_model:
                maybe = self.update_ground_race(pr, bucket_model=race_bucket_model)
                if maybe:
                    bm, val = maybe
                    gr_buffer.append((pr, bm, val))

            if len(batch) >= batch_size:
                # Flush to assign PersonRecord IDs, then write GroundRace for this batch
                self.session.flush()
                for person, bm, val in gr_buffer:
                    self.session.add(GroundRace(person_id=person.id, bucket_model=bm, value=val))
                gr_buffer.clear()
                batch.clear()

        # Final flush for trailing rows, then write remaining GroundRace
        self.session.flush()
        for person, bm, val in gr_buffer:
            self.session.add(GroundRace(person_id=person.id, bucket_model=bm, value=val))
        gr_buffer.clear()

        self.session.commit()
        return count

    # ---- Abstract standardizers & race calculator ----

    @abstractmethod
    def standardize_name(
        self, row: dict
    ) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        """Return (first, middle, last, name_suffix) pulled from raw row."""
        raise NotImplementedError

    @abstractmethod
    def standardize_address(
        self, row: dict
    ) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
        """Return (street_number, street_name, municipality, state, zip5) pulled from raw row."""
        raise NotImplementedError

    @abstractmethod
    def update_ground_race(
        self, person: PersonRecord, *, bucket_model: Optional[str]
    ) -> Optional[Tuple[str, str]]:
        """
        Compute ground-race label for this person. Read from person.raw and/or standardized fields.
        Return (bucket_model, value) to store, or None to skip.
        """
        raise NotImplementedError


# -----------------------------------
# Example subclass (pandas + mappings)
# -----------------------------------

class ExampleCsvIngestor(BaseIngestor):
    """
    Example for a CSV with columns:
      FirstName, MiddleName, LastName, StreetNo, StreetName, City, State, Zip, RaceBucket?
    """

    NAME_COLS = {
        "first": "FirstName",
        "middle": "MiddleName",
        "last": "LastName",
        "name_suffix": "NameSuffix",
    }
    ADDR_COLS = {
        "street_number": "StreetNo",
        "street_name": "StreetName",
        "municipality": "City",
        "state": "State",
        "zip5": "Zip",
    }

    def load_dataframe(self, path: str) -> pd.DataFrame:
        return pd.read_csv(path)

    def standardize_name(
        self, row: dict
    ) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        first = row.get(self.NAME_COLS["first"])
        middle = row.get(self.NAME_COLS["middle"])
        last = row.get(self.NAME_COLS["last"])
        name_suffix = row.get(self.NAME_COLS["name_suffix"])
        return first, middle, last, name_suffix

    def standardize_address(
        self, row: dict
    ) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
        street_no = row.get(self.ADDR_COLS["street_number"])
        street_nm = row.get(self.ADDR_COLS["street_name"])
        city = row.get(self.ADDR_COLS["municipality"])
        state = row.get(self.ADDR_COLS["state"])
        zip5 = row.get(self.ADDR_COLS["zip5"])
        return street_no, street_nm, city, state, zip5

    def update_ground_race(
        self, person: PersonRecord, *, bucket_model: Optional[str]
    ) -> Optional[Tuple[str, str]]:
        # Simple passthrough example: look for 'RaceBucket' in raw
        bm = (bucket_model or "example_v1")
        raw = person.raw or {}
        value = raw.get("RaceBucket") or raw.get("race_bucket")
        value = Helper._norm_str(value)
        if not value:
            return None
        return bm, value


class NC_VOTER_CSV_Ingestor(BaseIngestor):
    """North Carolina voter file example (tab-delimited latin1)."""

    def load_dataframe(self, path: str) -> pd.DataFrame:
        df = pd.read_csv(path, quotechar='"', sep="\t", compression="infer", dtype=str, encoding="latin1")
        df = df.applymap(lambda x: re.sub(r"\s+", " ", x.replace(",", " ")).strip() if isinstance(x, str) else x)
        return df

    def standardize_name(
        self, row: dict
    ) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        first = row.get("first_name")
        middle = row.get("middle_name")
        last = row.get("last_name")
        name_suffix = row.get("name_suffix_lbl")
        return first, middle, last, name_suffix

    def standardize_address(
        self, row: dict
    ) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
        res_street_address = row.get("res_street_address")

        sp = StreetParser()
        parts = sp.parse(s=res_street_address)

        street_no = parts.get("street_number")
        street_nm = sp.standardize(parts, case="lower")
        # If the standardized string includes the number, drop the first token
        if street_nm:
            street_nm = street_nm.split(" ", 1)[1] if " " in street_nm else street_nm

        city = row.get("res_city_desc")
        state = row.get("state_cd")
        zip5 = row.get("zip_code")
        return street_no, street_nm, city, state, zip5

    def update_ground_race(
        self, person: PersonRecord, *, bucket_model: Optional[str]
    ) -> Optional[Tuple[str, str]]:
        """
        bucket_model == 'race6' mapping using raw['race_code'] and raw['ethnic_code']:

        - If ethnic_code == 'HL'  -> value = 'hisp6' (ignore race_code)
        - Else:
            W -> white6
            B -> black6
            P -> native6
            A or I -> asian6
            O or U or M -> other6
        """
        bm = (bucket_model or "race6").lower()
        if bm != "race6":
            return None

        raw = person.raw or {}
        rcode = Helper._norm_str(raw.get("race_code"))
        ecode = Helper._norm_str(raw.get("ethnic_code"))

        if ecode and ecode.upper() == "HL":
            return "race6", "hisp6"

        if not rcode:
            return None

        code = rcode.upper()
        if code == "W":
            return "race6", "white6"
        if code == "B":
            return "race6", "black6"
        if code == "P":
            return "race6", "native6"
        if code in ("A", "I"):
            return "race6", "asian6"
        if code in ("O", "U", "M"):
            return "race6", "other6"

        return None
