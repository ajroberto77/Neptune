-- Neptune PostgreSQL setup — run ONCE as a superuser on the SAME instance that already
-- hosts the CATO databases (so cato_securities and the two neptune_* databases live
-- together; you can split them onto separate servers later with only a URL change).
--
--   psql -U postgres -h localhost -p 5432 -f scripts/postgres_setup.sql
--
-- Change the two passwords below before running. The app auto-creates its tables on first
-- startup (SQLAlchemy create_all), so no migrations are needed for a first run.

-- 1) Neptune's read/write role (owns the two Neptune databases).
CREATE ROLE neptune WITH LOGIN PASSWORD 'change-me-neptune';

-- 2) Neptune's own databases. Read/write; tables created by the app on boot.
CREATE DATABASE neptune_portfolios OWNER neptune;
CREATE DATABASE neptune_securities OWNER neptune;

-- 3) A SELECT-only role for the shared universe. Neptune must NEVER write to
--    cato_securities (read-only invariant, CLAUDE.md §1); the role is the enforcement,
--    not just convention. If cato_securities uses a schema other than `public`, change
--    the schema name in the three statements below.
CREATE ROLE neptune_ro WITH LOGIN PASSWORD 'change-me-ro';
\connect cato_securities
GRANT CONNECT ON DATABASE cato_securities TO neptune_ro;
GRANT USAGE ON SCHEMA public TO neptune_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO neptune_ro;
-- Future cato tables are readable too, without a re-grant.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO neptune_ro;
