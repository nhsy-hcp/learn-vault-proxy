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
CREATE ROLE preview_payments_rw NOLOGIN;
CREATE ROLE preview_checkout_rw NOLOGIN;
CREATE ROLE production_payments_rw NOLOGIN;
GRANT preview_payments_rw, preview_checkout_rw TO vault_admin_preview WITH ADMIN OPTION;
GRANT production_payments_rw TO vault_admin_production WITH ADMIN OPTION;

GRANT CONNECT ON DATABASE preview TO preview_payments_rw, preview_checkout_rw, vault_admin_preview;
GRANT CONNECT ON DATABASE production TO production_payments_rw, vault_admin_production;

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

GRANT USAGE ON SCHEMA payments TO production_payments_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA payments TO production_payments_rw;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA payments TO production_payments_rw;
