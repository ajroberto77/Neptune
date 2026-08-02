-- Runs once, automatically, on the docker-compose `db` service's first startup (mounted
-- into /docker-entrypoint-initdb.d/ — see docker-compose.yml). The image already creates
-- POSTGRES_DB (`neptune`, owned by POSTGRES_USER) before running scripts in that directory;
-- this just adds the three databases the app's three-DB topology actually expects
-- (src/neptune/config.py's portfolio_url/securities_url/macro_url). POSTGRES_USER already
-- owns them (a fresh cluster's bootstrap user is a superuser), so no role/grant statements
-- are needed here — this is the local dev stack, not the shared-instance production setup
-- in scripts/postgres_setup.sql.
CREATE DATABASE neptune_portfolios;
CREATE DATABASE neptune_securities;
CREATE DATABASE neptune_macro;
