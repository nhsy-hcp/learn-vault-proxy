-- Brings a lab whose PostgreSQL data predates the current postgres-bootstrap.sql up to date.
-- postgres-bootstrap.sql only runs on an empty data directory, so a kept PVC never sees its changes.
-- Run by scripts/install-postgres.sh on every run, as the postgres superuser. Safe to re-run: on a
-- fresh lab every statement is a no-op.
\set ON_ERROR_STOP on

-- Production group role: read-only on payments.items (replaced production_payments_rw)
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'production_payments_ro') THEN
    CREATE ROLE production_payments_ro NOLOGIN;
  END IF;
END
$$;
GRANT production_payments_ro TO vault_admin_production WITH ADMIN OPTION;
GRANT CONNECT ON DATABASE production TO production_payments_ro;

-- ---------------------------------------------------------------------------
-- Database: preview
-- ---------------------------------------------------------------------------
\connect preview

CREATE TABLE IF NOT EXISTS payments.card_tokens (
  id serial PRIMARY KEY,
  token text NOT NULL
);
INSERT INTO payments.card_tokens (token)
  SELECT 'preview-tok-001' WHERE NOT EXISTS (SELECT FROM payments.card_tokens);
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA payments TO preview_payments_rw;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA payments TO preview_payments_rw;

-- ---------------------------------------------------------------------------
-- Database: production
-- ---------------------------------------------------------------------------
\connect production

-- No grant at all: production app users must get "permission denied for table card_tokens"
CREATE TABLE IF NOT EXISTS payments.card_tokens (
  id serial PRIMARY KEY,
  token text NOT NULL
);
INSERT INTO payments.card_tokens (token)
  SELECT 'CANARY_PROD_CARD_TOKEN_NEVER_READ' WHERE NOT EXISTS (SELECT FROM payments.card_tokens);

GRANT USAGE ON SCHEMA payments TO production_payments_ro;
GRANT SELECT ON payments.items TO production_payments_ro;

-- Drop the old read-write role. Users Vault minted with it lose its rights at once; they expire
-- by max_ttl. Its privileges are revoked first, since DROP ROLE refuses a role that holds any.
DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'production_payments_rw') THEN
    REVOKE ALL ON ALL TABLES IN SCHEMA payments FROM production_payments_rw;
    REVOKE ALL ON ALL SEQUENCES IN SCHEMA payments FROM production_payments_rw;
    REVOKE ALL ON SCHEMA payments FROM production_payments_rw;
    REVOKE ALL ON DATABASE production FROM production_payments_rw;
    DROP ROLE production_payments_rw;
  END IF;
END
$$;
