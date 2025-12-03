from dotenv import load_dotenv
load_dotenv()

import os
from sqlalchemy import create_engine, text
import csv
import os
from pathlib import Path
from typing import Optional

DB_URL = os.environ["DATABASE_URL"] # designed to fail if not found


def update_address_table():
    engine = create_engine(DB_URL, future=True)
    sql = """
        INSERT INTO datalake.address (address_hash, address_canonical)
        SELECT pr.address_hash, pr.address_canonical
        FROM datalake.person_record pr
        LEFT JOIN datalake.address a
        ON a.address_hash = pr.address_hash
        WHERE pr.address_hash IS NOT NULL
        AND pr.address_hash <> ''
        AND a.address_hash IS NULL
        GROUP BY pr.address_hash, pr.address_canonical;  -- or SELECT DISTINCT
        """
    with engine.begin() as conn:
        result = conn.execute(text(sql))
        # Note: rowcount may be -1 for INSERT..SELECT on some drivers
        print("Executed INSERT…SELECT; rowcount (may be unreliable):", result.rowcount)

def export_all_addresses(*, batch_size: int = 5000, outfile: str = 'a.csv') -> None:
    engine = create_engine(DB_URL, future=True)

    outpath = Path(outfile)
    outpath.parent.mkdir(parents=True, exist_ok=True)

    # Prepare CSV
    with outpath.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["row", "address_canonical"])  # header

        rownum = 0
        last_key: Optional[str] = None

        while True:
            # Keyset pagination using the PK (address_hash)
            if last_key is None:
                stmt = text(
                    """
                    SELECT address_hash, address_canonical
                    FROM datalake.address
                    ORDER BY address_hash
                    LIMIT :lim
                    """
                )
                params = {"lim": batch_size}
            else:
                stmt = text(
                    """
                    SELECT address_hash, address_canonical
                    FROM datalake.address
                    WHERE address_hash > :last
                    ORDER BY address_hash
                    LIMIT :lim
                    """
                )
                params = {"last": last_key, "lim": batch_size}

            with engine.begin() as conn:
                rows = conn.execute(stmt, params).all()

            if not rows:
                break

            # Write this chunk
            for addr_hash, addr_canon in rows:
                rownum += 1
                writer.writerow([rownum, addr_canon])

            # Advance the keyset cursor
            last_key = rows[-1][0]

            print(f"[ok] wrote {len(rows):,} rows (total {rownum:,})")

    print(f"[done] wrote {rownum:,} rows to {outpath.resolve()}")



if __name__ == "__main__":
    update_address_table()
    export_all_addresses()