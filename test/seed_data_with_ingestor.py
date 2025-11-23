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

# your classes
from factories.base_ingestor import ExampleCsvIngestor   # from ingestors.py you wrote earlier

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
    ap = argparse.ArgumentParser(description="Postgres ingestion using ExampleCsvIngestor + Faker")
    ap.add_argument("--rows", type=int, default=200, help="Rows to generate")
    ap.add_argument("--outfile", type=str, default="people_seed.csv", help="CSV output path")
    ap.add_argument("--seed", type=int, default=42, help="Random seed")
    args = ap.parse_args()

    db_url = os.environ["DATABASE_URL"]  # fail fast if not set
    print(f"[info] Using DATABASE_URL={db_url}")

    # 1) Generate synthetic CSV
    generate_people_csv(args.outfile, args.rows, args.seed)
    print(f"[ok] Generated CSV: {os.path.abspath(args.outfile)} ({args.rows} rows)")

    # 2) Connect to Postgres (database & schema must already exist/migrated)
    engine = create_engine(db_url, future=True)
    SessionLocal = sessionmaker(bind=engine, future=True)

    # 3) Ingest via your ingestor
    with SessionLocal() as session:  # type: Session
        ing = ExampleCsvIngestor(session)

        data_file, info = ing.register_data_file(
            filename=os.path.basename(args.outfile),
            source="run_ingestion_pg.py",
            sha256_path=args.outfile,    # computes sha256 of file
            notes="Example ingestion (Faker)",
            row_count=args.rows,
        )

        if info["duplicate"]:
            print("[warn] Duplicate file detected by sha256 — skipping ingest.")
            return

        if info["conflict_filename"]:
            print("[warn] Filename conflict: same filename already exists with a different sha256. Proceeding with new record.")

        created = ing.ingest_rows(
            data_file=data_file,
            rows_iterable=ing.parse_rows_from_csv(args.outfile),
            batch_size=1000,
            standardize=True,  # fills standardized fields + canonical + hashes
        )

        print(f"[ok] Ingestion complete. PersonRecord created: {created}")

if __name__ == "__main__":
    main()
