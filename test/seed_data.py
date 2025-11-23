# seed_data.py
import os
import random
from datetime import datetime, timezone
from typing import List

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from faker import Faker
from dotenv import load_dotenv
load_dotenv() 
from sql_orm import (
    DataFile,
    PersonRecord,
    CensusGeocode,
    OtherGeocode,
)

# -------------------
# Configuration
# -------------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///seed.db")
DEFAULT_FILES = int(os.getenv("SEED_FILES", "2"))
DEFAULT_RECORDS_PER_FILE = int(os.getenv("SEED_RECORDS_PER_FILE", "50"))
DEFAULT_BENCHMARK = os.getenv("SEED_BENCHMARK", "2020")
DEFAULT_VINTAGE = os.getenv("SEED_VINTAGE", "2020")
RANDOM_SEED = int(os.getenv("SEED_RANDOM_SEED", "42"))

# Probabilities for Census outcome
P_MATCH = float(os.getenv("SEED_P_MATCH", "0.7"))
P_NO_MATCH = float(os.getenv("SEED_P_NO_MATCH", "0.2"))
P_AMBIGUOUS = float(os.getenv("SEED_P_AMBIGUOUS", "0.1"))

# If Census is NO_MATCH, chance we try a third-party and succeed there
P_OTHER_CREATED = float(os.getenv("SEED_P_OTHER_CREATED", "0.9"))
P_OTHER_MATCHED = float(os.getenv("SEED_P_OTHER_MATCHED", "0.6"))


def rand_geoid() -> str:
    """
    Make a plausible-looking GEOID-like string for testing.
    (Not authoritative; just synthetic.)
    """
    # e.g., concatenate a "state+county" lookalike with arbitrary digits
    return f"{random.randint(1, 56):02d}{random.randint(1, 999):03d}{random.randint(0, 999999999):09d}"


def make_persons(fake: Faker, n: int) -> List[PersonRecord]:
    persons = []
    for _ in range(n):
        first = fake.first_name()
        middle = fake.first_name() if random.random() < 0.25 else None
        last = fake.last_name()

        street_number = str(fake.random_int(min=1, max=9999))
        street_name = fake.street_name()
        municipality = fake.city()
        state = fake.state_abbr()
        zip5 = fake.postcode()[:5]  # ensure 5 chars

        raw = {
            "source": "faker",
            "v": 1,
            "original": {
                "first": first,
                "middle": middle,
                "last": last,
                "street_number": street_number,
                "street_name": street_name,
                "municipality": municipality,
                "state": state,
                "zip5": zip5,
            },
        }

        pr = PersonRecord(
            raw=raw,
            first_name=first,
            middle_name=middle,
            last_name=last,
            street_number=street_number,
            street_name=street_name,
            municipality=municipality,
            state=state,
            zip5=zip5,
        )
        persons.append(pr)
    return persons


def seed(
    files: int = DEFAULT_FILES,
    records_per_file: int = DEFAULT_RECORDS_PER_FILE,
    benchmark: str = DEFAULT_BENCHMARK,
    vintage: str = DEFAULT_VINTAGE,
):
    random.seed(RANDOM_SEED)
    fake = Faker()
    Faker.seed(RANDOM_SEED)

    engine = create_engine(DATABASE_URL, future=True)
    SessionLocal = sessionmaker(bind=engine, future=True)

    total_people = 0
    total_census = 0
    total_other = 0

    with SessionLocal() as session:  # type: Session
        now_utc = datetime.now(timezone.utc)

        for fidx in range(files):
            df = DataFile(
                filename=f"fake_file_{fidx+1}.csv",
                sha256=None,
                source="seed_data.py",
                row_count=records_per_file,
                notes="Synthetic data for testing",
            )
            session.add(df)
            session.flush()  # get df.id

            persons = make_persons(fake, records_per_file)
            for p in persons:
                p.file_id = df.id
                session.add(p)

            session.flush()  # assign ids to persons

            # Add geocoding results
            for p in persons:
                # Census geocode outcome
                r = random.random()
                if r < P_MATCH:
                    status = "matched"
                elif r < P_MATCH + P_NO_MATCH:
                    status = "no_match"
                else:
                    status = "ambiguous"

                cg = CensusGeocode(
                    record_id=p.id,
                    benchmark=benchmark,
                    vintage=vintage,
                    geoid=rand_geoid() if status == "matched" else None,
                    result={
                        "provider": "census",
                        "input": {
                            "street_number": p.street_number,
                            "street_name": p.street_name,
                            "municipality": p.municipality,
                            "state": p.state,
                            "zip5": p.zip5,
                        },
                        "status": status,
                    },
                    status=status,
                    notes=None,
                    geocoded_at=now_utc,  # optional; DB default fills if omitted
                )
                session.add(cg)
                total_census += 1

                # If Census failed, try a 3rd-party attempt sometimes
                if status == "no_match" and random.random() < P_OTHER_CREATED:
                    other_status = "matched" if random.random() < P_OTHER_MATCHED else "no_match"
                    lat = float(fake.latitude())
                    lng = float(fake.longitude())
                    req_str = f"{p.street_number} {p.street_name}, {p.municipality}, {p.state} {p.zip5}"

                    og = OtherGeocode(
                        record_id=p.id,
                        request=req_str,  # maps to DB column named "record"
                        result={
                            "provider": "third_party_xyz",
                            "request": req_str,
                            "candidates": 1 if other_status == "matched" else 0,
                        },
                        status=other_status,
                        latitude=lat if other_status == "matched" else None,
                        longitude=lng if other_status == "matched" else None,
                        notes="Auto-generated by seed",
                        geocoded_at=now_utc,
                    )
                    session.add(og)
                    total_other += 1

            total_people += len(persons)

        session.commit()

    print(f"[OK] Seed complete on {DATABASE_URL}")
    print(f"Files: {files}, People: {total_people}, CensusGeocodes: {total_census}, OtherGeocodes: {total_other}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Seed datalake.* tables with fake data.")
    ap.add_argument("--files", type=int, default=DEFAULT_FILES, help="Number of DataFile rows")
    ap.add_argument("--records-per-file", type=int, default=DEFAULT_RECORDS_PER_FILE, help="PersonRecord rows per DataFile")
    ap.add_argument("--benchmark", type=str, default=DEFAULT_BENCHMARK)
    ap.add_argument("--vintage", type=str, default=DEFAULT_VINTAGE)
    args = ap.parse_args()

    seed(
        files=args.files,
        records_per_file=args.records_per_file,
        benchmark=args.benchmark,
        vintage=args.vintage,
    )
