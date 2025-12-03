sync_address_sql="""
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