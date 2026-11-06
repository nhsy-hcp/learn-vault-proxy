-- PostgreSQL bootstrap for the Vault dynamic credentials lab. Runs once, on an empty data directory,
-- via 00-bootstrap.sh, which supplies the vault_admin_* bootstrap passwords from a Kubernetes Secret.
-- Vault rotates each admin password with db/rotate-root straight after configuration.
\set ON_ERROR_STOP on

CREATE DATABASE preview;
CREATE DATABASE production;
REVOKE CONNECT ON DATABASE preview, production, postgres FROM PUBLIC;

-- Vault's own logins: one per database, neither a superuser. CREATEROLE to mint users;
-- pg_signal_backend to end their sessions. Separate logins mean rotate-root on one connection
-- never breaks the other, and the preview admin can never grant a production group role.
CREATE ROLE vault_admin_preview WITH LOGIN CREATEROLE PASSWORD :'vault_admin_preview_password';
CREATE ROLE vault_admin_production WITH LOGIN CREATEROLE PASSWORD :'vault_admin_production_password';
GRANT pg_signal_backend TO vault_admin_preview, vault_admin_production;

-- Group roles (NOLOGIN, own nothing). PostgreSQL 16 needs ADMIN OPTION before a CREATEROLE user can grant them.
-- Preview roles are read-write on every table in their schema; the production role is read-only on one table.
CREATE ROLE preview_payments_rw NOLOGIN;
CREATE ROLE preview_checkout_rw NOLOGIN;
CREATE ROLE production_payments_ro NOLOGIN;
GRANT preview_payments_rw, preview_checkout_rw TO vault_admin_preview WITH ADMIN OPTION;
GRANT production_payments_ro TO vault_admin_production WITH ADMIN OPTION;

GRANT CONNECT ON DATABASE preview TO preview_payments_rw, preview_checkout_rw, vault_admin_preview;
GRANT CONNECT ON DATABASE production TO production_payments_ro, vault_admin_production;

-- ---------------------------------------------------------------------------
-- Database: preview
-- ---------------------------------------------------------------------------
\connect preview

CREATE SCHEMA payments;
CREATE TABLE payments.items (
  id serial PRIMARY KEY,
  name text NOT NULL,
  note text
);
INSERT INTO payments.items (name, note) VALUES
  ('preview-payment-001', 'card authorised'),
  ('preview-payment-002', 'refund pending');

CREATE TABLE payments.card_tokens (
  id serial PRIMARY KEY,
  token text NOT NULL
);
INSERT INTO payments.card_tokens (token) VALUES ('preview-tok-001');

CREATE SCHEMA checkout;
CREATE TABLE checkout.items (
  id serial PRIMARY KEY,
  name text NOT NULL,
  note text
);
INSERT INTO checkout.items (name, note) VALUES
  ('preview-basket-001', '3 items'),
  ('preview-basket-002', 'abandoned');

GRANT USAGE ON SCHEMA payments TO preview_payments_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA payments TO preview_payments_rw;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA payments TO preview_payments_rw;

GRANT USAGE ON SCHEMA checkout TO preview_checkout_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA checkout TO preview_checkout_rw;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA checkout TO preview_checkout_rw;

-- ---------------------------------------------------------------------------
-- Database: production
-- ---------------------------------------------------------------------------
\connect production

CREATE SCHEMA payments;
CREATE TABLE payments.items (
  id serial PRIMARY KEY,
  name text NOT NULL,
  note text
);
INSERT INTO payments.items (name, note) VALUES
  ('production-payment-001', 'settled'),
  ('CANARY_PROD_PAYMENTS_NEVER_LEAK', 'must never be returned to a preview app');

-- No grant at all: production app users must get "permission denied for table card_tokens"
CREATE TABLE payments.card_tokens (
  id serial PRIMARY KEY,
  token text NOT NULL
);
INSERT INTO payments.card_tokens (token) VALUES ('CANARY_PROD_CARD_TOKEN_NEVER_READ');

-- Read-only, one table: SELECT on items only; no DML, no sequences, nothing on card_tokens
GRANT USAGE ON SCHEMA payments TO production_payments_ro;
GRANT SELECT ON payments.items TO production_payments_ro;
