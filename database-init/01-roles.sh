#!/bin/sh
set -eu

: "${DATABASE_MIGRATOR_USER:?DATABASE_MIGRATOR_USER is required}"
: "${DATABASE_MIGRATOR_PASSWORD:?DATABASE_MIGRATOR_PASSWORD is required}"
: "${DATABASE_RUNTIME_USER:?DATABASE_RUNTIME_USER is required}"
: "${DATABASE_RUNTIME_PASSWORD:?DATABASE_RUNTIME_PASSWORD is required}"

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=migrator_user="$DATABASE_MIGRATOR_USER" \
  --set=migrator_password="$DATABASE_MIGRATOR_PASSWORD" \
  --set=runtime_user="$DATABASE_RUNTIME_USER" \
  --set=runtime_password="$DATABASE_RUNTIME_PASSWORD" \
  --set=database_name="$POSTGRES_DB" <<'SQL'
CREATE ROLE :"migrator_user" LOGIN PASSWORD :'migrator_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE :"runtime_user" LOGIN PASSWORD :'runtime_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;

ALTER DATABASE :"database_name" OWNER TO :"migrator_user";
GRANT CONNECT ON DATABASE :"database_name" TO :"runtime_user";
ALTER SCHEMA public OWNER TO :"migrator_user";
GRANT USAGE ON SCHEMA public TO :"runtime_user";

ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator_user" IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"runtime_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator_user" IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO :"runtime_user";
SQL
