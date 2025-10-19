from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy import insert

def upsert_geocode(session: Session, record_id: str, benchmark: str, vintage: str, geoid: str, result: dict, status="matched"):
    stmt = insert(VoterGeocode).values(
        record_id=record_id, benchmark=benchmark, vintage=vintage,
        geoid=geoid, result=result, status=status
    )
    # Use SQLAlchemy's generic "on conflict" helpers per dialect
    if session.bind.dialect.name in ("postgresql",):
        stmt = stmt.on_conflict_do_update(
            index_elements=[VoterGeocode.record_id, VoterGeocode.benchmark, VoterGeocode.vintage],
            set_=dict(geoid=geoid, result=result, status=status, geocoded_at=func.now())
        )
    elif session.bind.dialect.name in ("sqlite",):
        # SQLite uses INSERT OR REPLACE pattern via unique PK
        stmt = sqlite_insert(VoterGeocode).values(
            record_id=record_id, benchmark=benchmark, vintage=vintage,
            geoid=geoid, result=result, status=status
        ).prefix_with("OR REPLACE")
    else:
        # Fallback: try delete+insert
        session.query(VoterGeocode).filter_by(
            record_id=record_id, benchmark=benchmark, vintage=vintage
        ).delete(synchronize_session=False)
        session.execute(insert(VoterGeocode).values(
            record_id=record_id, benchmark=benchmark, vintage=vintage,
            geoid=geoid, result=result, status=status
        ))
        session.commit()
        return

    session.execute(stmt)
    session.commit()
