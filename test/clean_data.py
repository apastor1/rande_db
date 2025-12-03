# clean_data.py
import os
from sqlalchemy import create_engine, text, delete
from sqlalchemy.orm import Session, sessionmaker
from dotenv import load_dotenv
load_dotenv()

from sql_orm import DataFile, PersonRecord, CensusGeocode, OtherGeocode, Address


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///seed.db")


def soft_clean():
    """
    Portable cleanup: delete rows in child->parent order.
    Keeps tables and alembic_version intact.
    """
    engine = create_engine(DATABASE_URL, future=True)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as session:  # type: Session
        # Delete children first
        session.execute(delete(CensusGeocode))
        session.execute(delete(OtherGeocode))
        session.execute(delete(Address))
        session.execute(delete(PersonRecord))
        session.execute(delete(DataFile))
        session.commit()
    print(f"[OK] Soft clean complete on {DATABASE_URL} (all rows deleted)")


def hard_reset_schema(schema: str = "datalake"):
    """
    Postgres-only: DROP SCHEMA ... CASCADE; CREATE SCHEMA ...
    Use carefully—this removes tables and may also remove alembic_version
    if you store it in the same schema.
    """
    engine = create_engine(DATABASE_URL, future=True)
    if engine.dialect.name != "postgresql":
        raise SystemExit("Hard reset schema is only supported for PostgreSQL.")

    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    print(f"[OK] Hard reset: schema '{schema}' dropped & recreated on {DATABASE_URL}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Clean datalake.* tables.")
    ap.add_argument("--hard", action="store_true", help="Postgres only: drop & recreate the 'datalake' schema")
    ap.add_argument("--schema", type=str, default="datalake", help="Schema to drop/recreate for --hard")
    args = ap.parse_args()

    if args.hard:
        hard_reset_schema(args.schema)
    else:
        soft_clean()
