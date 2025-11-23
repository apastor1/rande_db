# run_ingestion_pg.py
from __future__ import annotations

import argparse
import csv
import os
import random

from dotenv import load_dotenv
load_dotenv()  # ensure DATABASE_URL is available from .env

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from faker import Faker

# Updated ingestor that uses pandas under the hood
from factories.base_ingestor import NC_VOTER_CSV_Ingestor  # your pandas-based ingestor


def generate_people_csv(path: str, n_rows: int, seed: int = 42) -> None:
    """
    Generate CSV with headers expected by ExampleCsvIngestor:
    FirstName, MiddleName, LastName, StreetNo, StreetName, City, State, Zip
    """
    random.seed(seed)
    fake = Faker()
    Faker.seed(seed)

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["FirstName", "MiddleName", "LastName", "StreetNo", "StreetName", "City", "State", "Zip"])
        for _ in range(n_rows):
            first = fake.first_name()
            middle = fake.first_name() if random.random() < 0.25 else ""
            last = fake.last_name()
            street_no = str(fake.random_int(min=1, max=9999))
            street_nm = fake.street_name()
            city = fake.city()
            state = fake.state_abbr()
            zip5 = fake.postcode()[:5]
            w.writerow([first, middle, last, street_no, street_nm, city, state, zip5])


def main():
    # ap = argparse.ArgumentParser(description="Postgres ingestion using ExampleCsvIngestor + Faker (pandas-first)")
    # ap.add_argument("--rows", type=int, default=200, help="Rows to generate")
    # ap.add_argument("--outfile", type=str, default="people_seed.csv", help="CSV output path")
    # ap.add_argument("--seed", type=int, default=42, help="Random seed")
    # ap.add_argument("--chunksize", type=int, default=None, help="pandas read_csv chunksize (streaming). Omit for full load")
    # ap.add_argument("--batch-size", type=int, default=1000, help="DB insert batch size")
    # ap.add_argument("--encoding", type=str, default="utf-8", help="CSV encoding passed to pandas.read_csv")
    # args = ap.parse_args()

    db_url = os.environ["DATABASE_URL"]  # fail fast if not set
    print(f"[info] Using DATABASE_URL={db_url}")

    # 1) Generate synthetic CSV
    #generate_people_csv(args.outfile, args.rows, args.seed)
    #print(f"[ok] Generated CSV: {os.path.abspath(args.outfile)} ({args.rows} rows)")

    # 2) Connect to Postgres (database & schema must already exist/migrated)
    engine = create_engine(db_url, future=True)
    SessionLocal = sessionmaker(bind=engine, future=True)

    # 3) Ingest via pandas-based ingestor
    with SessionLocal() as session:
        ing = NC_VOTER_CSV_Ingestor(session)
        path = "/home/andrei/Downloads/nc.junk.csv"
        data_file, info = ing.register_data_file(
            filename=os.path.basename(path),
            source="zzz",
            sha256_path=path,    # computes sha256 of file
            notes="Example ingestion (NC, pandas)",
        )

        if info.duplicate:
            print("[warn] Duplicate file detected by sha256 — skipping ingest.")
            return

        if info.conflict_filename:
            print("[warn] Filename conflict: same filename already exists with a different sha256. Proceeding with new record.")

        created = ing.ingest_csv(
            data_file=data_file,
            path=path,
            chunksize=None,      # None = load all; or stream in chunks
            batch_size=100,
            standardize=True,
        )

        print(f"[ok] Ingestion complete. PersonRecord created: {created}")


if __name__ == "__main__":
    main()
