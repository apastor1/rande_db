from sqlalchemy import create_engine, inspect
import os
from dotenv import load_dotenv
load_dotenv()
# Use your connection string (or pull from env)
db_url = os.getenv("DATABASE_URL", None)

engine = create_engine(db_url, future=True)
insp = inspect(engine)

# List all schemas
print("Schemas:", insp.get_schema_names())

# List tables in datalake
tables = insp.get_table_names(schema="datalake")
print("Tables in datalake:", tables)

# For each table, list columns
for table in tables:
    cols = insp.get_columns(table, schema="datalake")
    print(f"\nColumns for {table}:")
    for c in cols:
        print(f"  {c['name']} ({c['type']})")
