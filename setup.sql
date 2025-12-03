-- STEP 1
-- first run of psql
create database voter;
create user rux_rw with password 'LeaderComesFirst';
create user rux_ro with password 'LeaderComesLast';
grant all privileges on database voter to rux_rw;

-- STEP 2
-- psql -h 127.0.0.1 -U rux_rw -d voter -W
CREATE SCHEMA datalake;
-- grant rights to read only user
ALTER DEFAULT PRIVILEGES IN SCHEMA datalake GRANT SELECT ON TABLES TO rux_ro;
GRANT CONNECT ON DATABASE voter TO rux_ro;
GRANT USAGE ON SCHEMA datalake TO rux_ro;

GRANT USAGE ON SCHEMA public   TO rux_ro;
GRANT USAGE ON SCHEMA datalake TO rux_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public   TO rux_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA datalake TO rux_ro;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public   TO rux_ro;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA datalake TO rux_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT SELECT ON TABLES TO rux_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT USAGE, SELECT ON SEQUENCES TO rux_ro;

ALTER DEFAULT PRIVILEGES IN SCHEMA datalake
GRANT SELECT ON TABLES TO rux_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA datalake
GRANT USAGE, SELECT ON SEQUENCES TO rux_ro;


-- STEP 3
-- verify all grants
Verify effective permissions
\l+ voter                 -- check CONNECT
\dn+                      -- see schema privileges (look at "public" and "datalake")
\dp datalake.*            -- table/view privileges
\dp public.*              -- table/view privileges
\du+ rux_ro               -- role memberships, attributes
        ^
