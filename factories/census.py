import os
import re
import time
from itertools import islice

import pandas as pd
import requests
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# -----------------------------
# CONFIG
# -----------------------------
load_dotenv()

CENSUS_API_KEY = os.getenv("CENSUS_API_KEY")  # export CENSUS_API_KEY=...
PG_CONN_STR = os.getenv("DATABASE_URL")       # e.g. postgresql+psycopg2://user:pass@localhost:5432/db
TARGET_SCHEMA = "census"                      # will be created if missing


# -----------------------------
# HELPERS
# -----------------------------

# Regex that matches ONLY pandas/batch suffixes we might add (e.g., _m0, _m3221) at the end of a column name
_MERGE_SUFFIX_RE = re.compile(r"_m\d+$")

def _chunks(seq, n):
    """Yield successive n-sized chunks from a sequence."""
    it = iter(seq)
    while True:
        batch = list(islice(it, n))
        if not batch:
            break
        yield batch

def _is_census_var(col: str) -> bool:
    """
    True for canonical Census variable codes (e.g., B01001_001E, DP03_0062M).
    Pattern: capital letters + digits, underscore, 3 digits, then a trailing capital letter.
    """
    return bool(re.match(r"^[A-Z]+[0-9]+_[0-9]{3}[A-Z]$", col))

def _base_without_merge_suffix(col: str) -> str:
    return _MERGE_SUFFIX_RE.sub("", col)

def normalize_census_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize the final DataFrame before loading:
      - Remove ONLY duplicate key columns that have merge/batch suffixes (and verify identical values).
      - Preserve official Census variable codes EXACTLY as-is (e.g., B01001_001E).
      - Normalize a few geo/name columns to predictable names (lowercase keys, keep var codes unchanged).
      - Make GEOID where useful.
    """
    out = df.copy()

    # 1) Identify groups by base name (strip trailing _m#### only for comparison)
    groups = {}
    for c in out.columns:
        base = _base_without_merge_suffix(c)
        groups.setdefault(base, []).append(c)

    key_bases = {"NAME", "state", "county", "tract", "block group", "place"}
    drop_cols = []

    # 2) For each group with potential duplicates:
    for base, cols in groups.items():
        # Leave real Census variables entirely alone
        if _is_census_var(base):
            continue

        # Deduplicate repeated key columns (NAME/state/county/tract/block group/place)
        if base in key_bases and len(cols) > 1:
            first = out[cols[0]]
            for c in cols[1:]:
                if not out[c].equals(first):
                    raise ValueError(f"Key column conflict for '{base}': {cols[0]} != {c}")
            # all identical → drop the extras
            drop_cols.extend(cols[1:])

    if drop_cols:
        out = out.drop(columns=drop_cols)

    # 3) Tidy geo/name columns ONLY (do not touch variable codes)
    rename_map = {
        "NAME": "name",
        "block group": "block_group",
    }
    out = out.rename(columns=rename_map)

    # 4) Ensure geo IDs are strings (FIPS semantics)
    for key in ("state", "county", "tract", "block_group", "place"):
        if key in out.columns:
            out[key] = out[key].astype(str)

    # 5) Optional: create GEOID for county-level (2 + 3 digits)
    if {"state", "county"}.issubset(out.columns) and "geoid" not in out.columns:
        out["geoid"] = out["state"].str.zfill(2) + out["county"].str.zfill(3)

    # 6) Cast variable columns to numeric when possible; leave keys and name alone
    protected = {"name", "state", "county", "tract", "block_group", "place", "geoid"}
    for c in out.columns:
        if c not in protected and _is_census_var(c):
            out[c] = pd.to_numeric(out[c], errors="ignore")

    # 7) Safety: ensure no duplicate column names remain
    dups = out.columns[out.columns.duplicated()]
    if len(dups):
        raise ValueError(f"Duplicate columns after normalize: {list(dups)}")

    return out

def fetch_table_for_geo(
    year: int,
    dataset_path: str,
    table_vars: list,
    geo: str,
    within: str | None = None,
    max_vars_per_call: int = 48,
) -> pd.DataFrame:
    endpoint = f"https://api.census.gov/data/{year}/{dataset_path}"

    all_batches = list(_chunks(table_vars, max_vars_per_call))
    if not all_batches:
        raise ValueError("No variables to request.")

    merged = None
    join_keys = None  # determined from the first batch

    for i, batch_vars in enumerate(all_batches):
        params = {"get": ",".join(["NAME"] + batch_vars), "for": f"{geo}:*"}
        if within:
            params["in"] = within
        if CENSUS_API_KEY:
            params["key"] = CENSUS_API_KEY

        r = requests.get(endpoint, params=params, timeout=120)
        try:
            r.raise_for_status()
        except requests.HTTPError as e:
            raise requests.HTTPError(
                f"{e}\nEndpoint: {endpoint}\nVars in this batch ({len(batch_vars)}): {batch_vars[:8]}..."
            ) from e

        data = r.json()
        df_batch = pd.DataFrame(data[1:], columns=data[0])

        if merged is None:
            merged = df_batch
            # detect available geo key columns from the first batch
            candidate_keys = ["state", "county", "tract", "block group", "place", geo]
            join_keys = [k for k in candidate_keys if k in merged.columns]
            for k in join_keys:
                merged[k] = merged[k].astype(str)
        else:
            # keep join keys present; drop only NAME to prevent dup
            keep_cols = [c for c in df_batch.columns if c != "NAME"]
            df_right = df_batch[keep_cols].copy()

            # sanity: ensure the keys exist on the right
            missing = [k for k in join_keys if k not in df_right.columns]
            if missing:
                raise KeyError(
                    f"Join key(s) {missing} not found in batch {i}. "
                    f"Right columns (sample): {list(df_right.columns)[:10]}..."
                )

            # **CRITICAL GUARD**: Ensure no variable overlap between left & right (besides join keys)
            overlap = [c for c in df_right.columns if c in merged.columns and c not in join_keys]
            if overlap:
                raise ValueError(
                    f"Variable(s) appear in multiple batches: {overlap[:10]} "
                    f"(batch {i}). This indicates a bad chunk split."
                )

            # align dtypes for join keys
            for k in join_keys:
                merged[k] = merged[k].astype(str)
                df_right[k] = df_right[k].astype(str)

            merged = merged.merge(df_right, on=join_keys, how="inner")

    return merged


def benchmark_to_dataset(year: int, benchmark: str, table: str, use_profile=False):
    """
    Map a friendly 'benchmark' to a Census API dataset path.
    """
    b = benchmark.lower()
    if b in ["acs5", "acs_5", "acs-5"]:
        return "acs/acs5" if not use_profile else "acs/acs5/profile"
    if b in ["acs1", "acs_1", "acs-1"]:
        return "acs/acs1" if not use_profile else "acs/acs1/profile"
    if b in ["profile", "dp", "acs_dp"]:
        return "acs/acs5/profile"  # default to 5-year profile
    if b in ["dec_pl", "pl"]:
        return f"dec/{year}/pl"
    if b in ["dec_sf1", "sf1"]:
        return f"dec/{year}/sf1"
    if b.startswith("pep"):
        # Adjust per PEP endpoint as needed
        return "pep/population"
    raise ValueError(f"Unknown benchmark: {benchmark}")


def get_variable_list(year: int, dataset_path: str, table_prefix: str, include_margins: bool = False):
    """
    Read variables.json and select ONLY the exact table (e.g., B01001_*),
    optionally including margins of error.
    """
    url = f"https://api.census.gov/data/{year}/{dataset_path}/variables.json"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    vars_json = resp.json()["variables"]

    # exact table code match before underscore
    keep = {k: v for k, v in vars_json.items() if k.split("_", 1)[0] == table_prefix}

    if not include_margins:
        # estimates only (E). Keep NAME out of this dict; NAME is not a var
        keep = {k: v for k, v in keep.items() if k.endswith("E")}

    return keep

def upsert_to_postgres(df: pd.DataFrame, table_name: str):
    engine = create_engine(PG_CONN_STR, pool_pre_ping=True)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {TARGET_SCHEMA};"))
        try:
            df.to_sql(
                table_name,
                conn,
                schema=TARGET_SCHEMA,
                if_exists="replace",
                index=False,
                chunksize=5000,
                method="multi",
            )
        except Exception as e:
            # Helpful diagnostics
            print(f"[to_sql FAILED] table={TARGET_SCHEMA}.{table_name}")
            print(f"Columns ({len(df.columns)}): {list(df.columns)[:12]} ...")
            print(f"dtypes head:\n{df.dtypes.head(12)}")
            raise

        # indexes...
        for gc in ["state", "county", "tract", "block_group", "place", "geoid"]:
            if gc in df.columns:
                conn.execute(
                    text(
                        f'CREATE INDEX IF NOT EXISTS idx_{table_name}_{gc} '
                        f'ON {TARGET_SCHEMA}."{table_name}" ("{gc}")'
                    )
                )
        if {"state", "county"}.issubset(df.columns):
            conn.execute(
                text(
                    f'CREATE INDEX IF NOT EXISTS idx_{table_name}_state_county '
                    f'ON {TARGET_SCHEMA}."{table_name}" ("state","county")'
                )
            )


def DEFUNCT_upsert_to_postgres(df: pd.DataFrame, table_name: str):
    """
    Replace the table (idempotent load), then create useful indexes.
    """
    engine = create_engine(PG_CONN_STR, pool_pre_ping=True)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {TARGET_SCHEMA};"))

        dups = df.columns[df.columns.duplicated()]
        if len(dups):
            raise ValueError(f"Duplicate columns after merge/normalize: {list(dups)}")

        df.to_sql(
            table_name,
            conn,
            schema=TARGET_SCHEMA,
            if_exists="replace",
            index=False,
            chunksize=5000,
            method="multi",
        )

        # index whichever geo keys exist
        for gc in ["state", "county", "tract", "block_group", "place", "geoid"]:
            if gc in df.columns:
                conn.execute(
                    text(
                        f'CREATE INDEX IF NOT EXISTS idx_{table_name}_{gc} '
                        f'ON {TARGET_SCHEMA}."{table_name}" ("{gc}")'
                    )
                )

        # common composite for county
        if {"state", "county"}.issubset(df.columns):
            conn.execute(
                text(
                    f'CREATE INDEX IF NOT EXISTS idx_{table_name}_state_county '
                    f'ON {TARGET_SCHEMA}."{table_name}" ("state","county")'
                )
            )


def download_and_store(
    table: str,
    year: int,
    benchmark: str,
    geography: str = "county",
    scope: str = "state:*",
    use_profile: bool = False,
    sleep_between: float = 0.0,
    include_margins: bool = False,
) -> str:
    """
    Example calls:
      - download_and_store("B01001", 2023, "acs5", "county", "state:*")
      - download_and_store("DP03", 2023, "profile", "county", "state:*", use_profile=True)
      - download_and_store("P1", 2020, "dec_pl", "county", "state:*")

    Parameters
    ----------
    table : Census table prefix, e.g., "B01001", "DP03", "P1"
    year : dataset year
    benchmark : 'acs5', 'acs1', 'profile', 'dec_pl', 'dec_sf1', 'pep'
    geography : target geo (e.g. 'state', 'county', 'tract', 'block group')
    scope : value for the "in=" parameter (e.g., 'state:*' so counties are per-state)
    use_profile : if True, route ACS to /profile datasets
    sleep_between : throttle between requests (useful if you expand to per-state loops)
    include_margins : include MOE ('M') variables alongside estimates if True

    Returns
    -------
    The fully-qualified table name (schema.table)
    """
    dataset_path = benchmark_to_dataset(year, benchmark, table, use_profile=use_profile)

    # 1) Resolve variables for the requested table prefix
    var_meta = get_variable_list(year, dataset_path, table, include_margins=include_margins)
    table_vars = sorted(var_meta.keys())
    if not table_vars:
        raise ValueError(f"No variables found for table '{table}' in {year}/{dataset_path}")

    # 2) Pull the data (batched + suffix-safe merge)
    df = fetch_table_for_geo(year, dataset_path, table_vars, geo=geography, within=scope)

    # 3) Build labels (for meta table) directly from vars.json
    label_map = {k: var_meta[k].get("label", k) for k in table_vars}

    # 4) Normalize (keys only; keep variable codes as-is)
    df = normalize_census_columns(df)

    # 5) Write to Postgres
    safe_table = f"{table.lower()}_{benchmark.lower()}_{year}_{geography}"

    upsert_to_postgres(df, safe_table)

    # 6) (Optional) Create a metadata side-table with labels
    meta_df = pd.DataFrame([{"variable": k, "label": label_map[k]} for k in table_vars])
    upsert_to_postgres(meta_df, f"{safe_table}__meta")

    time.sleep(sleep_between)
    return f'{TARGET_SCHEMA}."{safe_table}"'


if __name__ == "__main__":
    # All counties, ACS 5-year, table B01001 (sex by age), 2023
    download_and_store(table="B01001", year=2023, benchmark="acs5", geography="county", scope="state:*")

    # ACS 5-year Data Profile DP03 (economic), 2023
    download_and_store(table="DP03", year=2023, benchmark="profile", geography="county", scope="state:*", use_profile=True)

    # 2020 Decennial PL (redistricting) table P1 (total population) at county level
    download_and_store(table="P1", year=2020, benchmark="dec_pl", geography="county", scope="state:*")
