#Design (tables)

voter_file
One row per ingested file (provenance, dedupe).
Columns: id, filename, sha256, source, received_at, row_count, notes.

voter_record
One row per raw record (from any file).
Columns: id, file_id (FK), raw (JSONB original), standardized name/address fields, plus generated canonical strings and hashes:
first_name, middle_name, last_name
street_number, street_name, municipality, state, zip5
name_canonical (generated), address_canonical (generated)
name_hash (generated), address_hash (generated)

Useful indexes on hashes and common filters.

voter_geocode
Many per record, keyed by (record_id, benchmark, vintage).
Columns: record_id (FK), benchmark, vintage, geoid, result (JSONB), status, geocoded_at.

