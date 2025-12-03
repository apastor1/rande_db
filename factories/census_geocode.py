# geocoding_run.py
from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()  # your preference: always load .env

import os
import time
import hashlib
from typing import Iterable, List, Optional, Tuple, Dict, Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sql_orm import CensusGeocode, new_uuid_str  # from your models


# ---------- Census client (pluggable) ----------

class CensusClient:
    """
    Abstract client. Implement `send_batch` for the real Census API.
    Must return a list of dicts: {"address_hash", "geoid", "status", "result"}.
    - address_hash: string (matches input)
    - geoid: string or None
    - status: e.g., "matched" | "no_match" | "ambiguous"
    - result: arbitrary JSON payload from provider
    """
    def send_batch(
        self,
        rows: List[Tuple[str, str]],
        *,
        benchmark: str,
        vintage: str,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError


class MockCensusClient(CensusClient):
    """
    Mock implementation for local testing. Pretends to "geocode" addresses.
    - If canonical contains "po box" => no_match
    - Else returns a fake GEOID derived from md5(address_canonical).
    """
    def send_batch(
        self,
        rows: List[Tuple[str, str]],
        *,
        benchmark: str,
        vintage: str,
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for address_hash, canonical in rows:
            canon_lc = (canonical or "").lower()
            if "po box" in canon_lc:
                out.append({
                    "address_hash": address_hash,
                    "geoid": None,
                    "status": "no_match",
                    "result": {"reason": "po_box_filtered", "benchmark": benchmark, "vintage": vintage},
                })
            else:
                # Derive a deterministic fake GEOID for demo purposes
                geoid = hashlib.md5(canon_lc.encode("utf-8")).hexdigest()[:12]
                out.append({
                    "address_hash": address_hash,
                    "geoid": geoid,
                    "status": "matched",
                    "result": {"mock": True, "benchmark": benchmark, "vintage": vintage},
                })
        # Simulate network latency
        time.sleep(0.2)
        return out


# ---------- DB helpers ----------

FETCH_FIRST_SQL = text("""
    SELECT a.address_hash, a.address_canonical
    FROM datalake.address a
    LEFT JOIN datalake.census_geocode g
      ON g.address_hash = a.address_hash
     AND g.benchmark = :benchmark
     AND g.vintage   = :vintage
    WHERE g.address_hash IS NULL
    ORDER BY a.address_hash
    LIMIT :lim
""")

FETCH_NEXT_SQL = text("""
    SELECT a.address_hash, a.address_canonical
    FROM datalake.address a
    LEFT JOIN datalake.census_geocode g
      ON g.address_hash = a.address_hash
     AND g.benchmark = :benchmark
     AND g.vintage   = :vintage
    WHERE g.address_hash IS NULL
      AND a.address_hash > :last
    ORDER BY a.address_hash
    LIMIT :lim
""")

def fetch_batch(session: Session, *, benchmark: str, vintage: str, last_key: Optional[str], limit: int) -> List[Tuple[str, str]]:
    if last_key is None:
        rows = session.execute(FETCH_FIRST_SQL, {"benchmark": benchmark, "vintage": vintage, "lim": limit}).all()
    else:
        rows = session.execute(FETCH_NEXT_SQL, {"benchmark": benchmark, "vintage": vintage, "last": last_key, "lim": limit}).all()
    return [(r[0], r[1]) for r in rows]


def upsert_census_results(session: Session, *, benchmark: str, vintage: str, results: List[Dict[str, Any]]) -> int:
    """
    Upsert into datalake.census_geocode using Postgres ON CONFLICT.
    Expects `CensusGeocode` unique key on (address_hash, benchmark, vintage).
    """
    if not results:
        return 0

    rows = []
    for rec in results:
        rows.append({
            "id": new_uuid_str(),                # PK (string UUID)
            "address_hash": rec["address_hash"],
            "benchmark": benchmark,
            "vintage": vintage,
            "geoid": rec.get("geoid"),
            "result": rec.get("result"),
            "status": rec.get("status"),
            # geocoded_at uses server_default=now(); omit to let DB fill it
            "notes": rec.get("notes"),
        })

    stmt = (
        pg_insert(CensusGeocode)
        .values(rows)
        .on_conflict_do_update(
            index_elements=[CensusGeocode.address_hash, CensusGeocode.benchmark, CensusGeocode.vintage],
            set_={
                "geoid": pg_insert(CensusGeocode).excluded.geoid,
                "result": pg_insert(CensusGeocode).excluded.result,
                "status": pg_insert(CensusGeocode).excluded.status,
                # refresh timestamp on update if you prefer:
                # "geocoded_at": func.now(),
                "notes": pg_insert(CensusGeocode).excluded.notes,
            }
        )
    )
    res = session.execute(stmt)
    # For INSERT..ON CONFLICT DO UPDATE, rowcount is the total affected (inserted+updated) rows.
    return res.rowcount or 0


# ---------- Orchestration loop ----------

def run_geocoding(*, benchmark: str, vintage: str, batch_size: int = 5000, client: CensusClient | None = None) -> None:
    db_url = os.environ["DATABASE_URL"]  # fail fast if missing
    engine = create_engine(db_url, future=True)

    if client is None:
        client = MockCensusClient()

    total_sent = 0
    total_upserted = 0
    last_key: Optional[str] = None
    chunk_idx = 0

    with Session(engine, future=True) as session:
        while True:
            # 1) fetch next chunk of addresses that do NOT have a geocode for (benchmark, vintage)
            batch = fetch_batch(session, benchmark=benchmark, vintage=vintage, last_key=last_key, limit=batch_size)
            if not batch:
                break

            # 2) send to Census (client decides HTTP / batch flow)
            results = client.send_batch(batch, benchmark=benchmark, vintage=vintage)

            # 3) upsert results
            affected = upsert_census_results(session, benchmark=benchmark, vintage=vintage, results=results)
            session.commit()

            total_sent += len(batch)
            total_upserted += affected
            chunk_idx += 1
            last_key = batch[-1][0]  # advance keyset cursor

            print(f"[chunk {chunk_idx}] sent={len(batch):,} upserted={affected:,} total_sent={total_sent:,}")

    print(f"[done] total_sent={total_sent:,} total_upserted={total_upserted:,} benchmark={benchmark} vintage={vintage}")


# ---------- CLI ----------

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Run a geocoding session in 5k chunks")
    ap.add_argument("--benchmark", default="2020")
    ap.add_argument("--vintage", default="2020")
    ap.add_argument("--batch-size", type=int, default=5000)
    ap.add_argument("--mock", action="store_true", help="Use mock client (default if no client is wired)")
    args = ap.parse_args()

    # For now we only wire MockCensusClient; replace with a real client when ready.
    client = MockCensusClient()

    run_geocoding(
        benchmark=args.benchmark,
        vintage=args.vintage,
        batch_size=args.batch_size,
        client=client,
    )
