# Design (tables)

## `voter_file`
One row per ingested file (provenance, dedupe).

**Columns**
- `id`
- `filename`
- `sha256`
- `source`
- `received_at`
- `row_count`
- `notes`

---

## `voter_record`
One row per raw record (from any file).

**Columns**
- `id`
- `file_id` (FK → `voter_file.id`)
- `raw` (JSON/JSONB original)

**Standardized name fields**
- `first_name`
- `middle_name`
- `last_name`

**Standardized address fields**
- `street_number`
- `street_name`
- `municipality`
- `state`
- `zip5`

**Canonical (generated/materialized)**
- `name_canonical`
- `address_canonical`

**Hashes (generated/materialized)**
- `name_hash`
- `address_hash`

**Useful indexes**
- `file_id`
- `name_hash`
- `address_hash`
- `zip5`
- `state`
- `municipality`

---

## `voter_geocode`
Many per record, keyed by `(record_id, benchmark, vintage)`.

**Columns**
- `record_id` (FK → `voter_record.id`) **(PK part)**
- `benchmark` **(PK part)**
- `vintage` **(PK part)**
- `geoid`
- `result` (JSON/JSONB)
- `status`
- `geocoded_at`
