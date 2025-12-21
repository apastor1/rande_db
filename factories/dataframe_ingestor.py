from __future__ import annotations

# Always load .env first (per your preference)
from dotenv import load_dotenv
load_dotenv()

from tqdm import tqdm
import hashlib
import os,datetime
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

import pandas as pd
from factories.street_parser import StreetParser

# -------------------------------------------------------------------
# Simple canonicalization + hashing helpers (no DB, no sql_orm)
# -------------------------------------------------------------------

def canon_name(
    first: Optional[str],
    middle: Optional[str],
    last: Optional[str],
    suffix: Optional[str],
) -> Optional[str]:
    """
    Very simple canonical name:
    - lowercased
    - extra whitespace trimmed
    - components joined by single spaces
    """
    parts = []
    for part in (first, middle, last, suffix):
        if part:
            s = str(part).strip().lower()
            if s:
                parts.append(s)
    if not parts:
        return None
    return " ".join(parts)


def canon_addr(
    street_number: Optional[str],
    street_name: Optional[str],
    municipality: Optional[str],
    state: Optional[str],
    zip5: Optional[str],
) -> Optional[str]:
    """
    Simple canonical address:
    - lowercased
    - missing components skipped
    - joined by ' , '
    """
    parts = []
    for part in (street_number, street_name, municipality, state, zip5):
        if part:
            s = str(part).strip().lower()
            if s:
                parts.append(s)
    if not parts:
        return None
    return " , ".join(parts)


def hex_md5(s: Optional[str]) -> Optional[str]:
    """
    Return hex MD5 of the given string, or None if input is None/empty.
    """
    if not s:
        return None
    return hashlib.md5(s.encode("utf-8")).hexdigest()


# ----------------------
# Utilities
# ----------------------

class Helper:
    @staticmethod
    def parse_street_address(addr: str) -> Tuple[Optional[str], str]:
        """
        Split a street address into leading numeric part and the remainder.

        Examples:
        '100 Main Str'   -> ('100', 'Main Str')
        '  42  Broadway' -> ('42', 'Broadway')
        'Main Str 100'   -> (None, 'Main Str 100')
        ''               -> (None, '')

        If the first word is not all digits, returns (None, original_string_stripped).
        """
        s = (addr or "").strip()
        m = re.match(r"^(\d+)\b\s*(.*)$", s)
        if m:
            number = m.group(1)
            rest = m.group(2).strip()
            return number, rest
        return None, s

    @staticmethod
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
# Base ingestor (pandas-only)
# ----------------------

class BaseDFIngestor(ABC):
    """
    Base class for ingesting via pandas, with NO database.

    Responsibilities:
      a) load a DataFrame from a file (subclass can override)
      b) standardize name fields
      c) standardize address fields
      d) add canonical + hash columns

    Subclasses implement:
      - standardize_name(row: dict) -> (first, middle, last, suffix)
      - standardize_address(row: dict) -> (street_number, street_name, municipality, state, zip5)
    """

    # ---- CSV loaders using pandas ----

    def load_dataframe(self, path: str, **read_csv_kwargs) -> pd.DataFrame:
        """
        Load entire CSV/TSV into memory via pandas.read_csv.
        Subclasses can override to customize defaults (dtype, encoding, etc.).
        """
        return pd.read_csv(path, **read_csv_kwargs)

    # ---- Core standardization logic -> returns new DataFrame ----

    def process_dataframe(
        self,
        df: pd.DataFrame,
        standardize: bool = True,
    ) -> pd.DataFrame:
        """
        Take a DataFrame of raw rows and return a new DataFrame with
        additional standardized / canonical / hash columns.

        Does NOT touch any database; everything is kept in-memory.
        """
        if not standardize:
            # If caller just wants normalized strings but not added columns,
            # we can still return a shallow copy.
            return df.copy()

        df_out = df.copy()

        # Prepare new columns
        df_out["first_name_std"] = None
        df_out["middle_name_std"] = None
        df_out["last_name_std"] = None
        df_out["name_suffix_std"] = None
        df_out["name_canonical"] = None
        df_out["name_hash"] = None

        df_out["street_number_std"] = None
        df_out["street_name_std"] = None
        df_out["municipality_std"] = None
        df_out["state_std"] = None
        df_out["zip5_std"] = None
        df_out["address_canonical"] = None
        df_out["address_hash"] = None

        # Work with dict rows for convenience
        rows = df_out.to_dict(orient="records")
        new_rows: List[dict] = []

        #for row in rows:
        for row in tqdm(rows, desc="Standardizing rows"):
            # Normalize raw dict: turn NaN into None, strip whitespace
            normalized_raw = {
                k: Helper._norm_str(v) if not isinstance(v, (dict, list)) else v
                for k, v in row.items()
            }

            # (c) Name
            first, middle, last, name_suffix = self.standardize_name(normalized_raw)
            first_n = Helper._norm_str(first)
            middle_n = Helper._norm_str(middle)
            last_n = Helper._norm_str(last)
            suffix_n = Helper._norm_str(name_suffix)

            if True:
                name_canon = canon_name(first_n, "", last_n, "")
            else:
                name_canon = canon_name(first_n, middle_n, last_n, suffix_n)
            name_hash = hex_md5(name_canon)

            normalized_raw["first_name_std"] = first_n
            normalized_raw["middle_name_std"] = middle_n
            normalized_raw["last_name_std"] = last_n
            normalized_raw["name_suffix_std"] = suffix_n
            normalized_raw["name_canonical"] = name_canon
            normalized_raw["name_hash"] = name_hash

            # (d) Address
            street_number, street_name, municipality, state, zip5 = self.standardize_address(normalized_raw)

            street_number_n = Helper._norm_str(street_number)
            street_name_n = Helper._norm_str(street_name)
            municipality_n = Helper._norm_str(municipality)
            state_n = Helper._norm_str(state)
            zip5_n = Helper._norm_str(zip5)
            if zip5_n:
                zip5_n = zip5_n[:5]

            addr_canon = canon_addr(
                street_number_n,
                street_name_n,
                municipality_n,
                state_n,
                zip5_n,
            )
            addr_hash = hex_md5(addr_canon)

            normalized_raw["street_number_std"] = street_number_n
            normalized_raw["street_name_std"] = street_name_n
            normalized_raw["municipality_std"] = municipality_n
            normalized_raw["state_std"] = state_n
            normalized_raw["zip5_std"] = zip5_n
            normalized_raw["address_canonical"] = addr_canon
            normalized_raw["address_hash"] = addr_hash

            new_rows.append(normalized_raw)

        return pd.DataFrame(new_rows)

    # Convenience: load+process in one go
    def load_and_standardize(
        self,
        path: str,
        standardize: bool = True,
        **read_csv_kwargs,
    ) -> pd.DataFrame:
        """
        High-level helper:
          1) load a DataFrame from path
          2) standardize name/address & add canonical/hash columns
        """
        df = self.load_dataframe(path, **read_csv_kwargs)
        return self.process_dataframe(df, standardize=standardize)

    # ---- Abstract standardizers ----

    @abstractmethod
    def standardize_name(self, row: dict) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        """Return (first, middle, last, name_suffix) pulled from raw row."""
        raise NotImplementedError

    @abstractmethod
    def standardize_address(
        self,
        row: dict,
    ) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
        """Return (street_number, street_name, municipality, state, zip5) pulled from raw row."""
        raise NotImplementedError



# -----------------------------------
# NC Voter File Ingestor (no DB)
# -----------------------------------

class NC_DF_VOTER_CSV_Ingestor(BaseDFIngestor):
    """
    Ingestor for NC voter TSV file.

    - Loads TSV with:
        quotechar='"', sep='\t', compression='infer', dtype=str, encoding='latin1'
    - Cleans whitespace and commas in string fields
    - Standardizes name & address using NC-specific columns
    - Returns a pandas DataFrame with extra standardized/canonical/hash columns.
    """

    def load_dataframe(self, path: str, **read_csv_kwargs) -> pd.DataFrame:
        # Default kwargs for NC voter file; caller can override via **read_csv_kwargs
        kwargs = {
            "quotechar": '"',
            "sep": "\t",
            "compression": "infer",
            "dtype": str,
            "encoding": "latin1",
        }
        kwargs.update(read_csv_kwargs)

        print(f"{datetime.datetime.now()} Loading {path}")
        df = pd.read_csv(path, **kwargs)
        print(f"{datetime.datetime.now()} Loaded {path}")

        # Clean up strings: replace commas with space, collapse whitespace, strip
        df = df.applymap(
            lambda x: re.sub(r"\s+", " ", x.replace(",", " ")).strip()
            if isinstance(x, str)
            else x
        )
        print(f"{datetime.datetime.now()} applymap finished {path}")

        return df

    def standardize_name(self, row: dict) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        # Uses NC-specific columns
        first = row.get("first_name")
        middle = row.get("middle_name")
        last = row.get("last_name")
        # Note: original had "name_suffi x_lbl" typo; assume intended "name_suffix_lbl"
        name_suffix = row.get("name_suffix_lbl") or row.get("name_suffx_lbl") or row.get("name_suffi x_lbl")
        return first, middle, last, name_suffix

    def standardize_address(
        self,
        row: dict,
    ) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
        res_street_address = row.get("res_street_address") or ""

        sp = StreetParser()
        parts = sp.parse(s=res_street_address)

        street_no = parts.get("street_number")
        # StreetParser already knows how to standardize
        street_nm = sp.standardize(parts, case="lower", include_street_number=False)

        city = row.get("res_city_desc")
        state = row.get("state_cd")
        zip5 = row.get("zip_code")
        return street_no, street_nm, city, state, zip5
